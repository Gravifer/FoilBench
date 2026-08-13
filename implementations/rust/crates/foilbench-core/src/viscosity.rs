//! Deterministic implicit viscosity for the shared staggered grid.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::too_many_arguments,
    clippy::too_many_lines
)]

use crate::{
    field::{MacGrid2, ScalarField2, VectorField2},
    grid::{GridDomain2, apply_domain_boundaries, enforce_solid_faces},
    solver::{FailureReason, FailureStage, FlowScalar, SolverError},
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DiffusionReport {
    pub converged: bool,
    pub iterations: usize,
    pub residual_linf: f64,
}

fn is_fixed_u(solid: &ScalarField2<u8>, domain: GridDomain2, x: usize, y: usize) -> bool {
    if (!domain.periodic_x && (x == 0 || x == domain.nx()))
        || (!domain.periodic_y && (y == 0 || y + 1 == domain.ny()))
    {
        return true;
    }
    let left = (x > 0).then(|| x - 1);
    let right = (x < domain.nx()).then_some(x);
    left.is_some_and(|cell| solid.get(cell, y) != 0)
        || right.is_some_and(|cell| solid.get(cell, y) != 0)
}

fn is_fixed_v(solid: &ScalarField2<u8>, domain: GridDomain2, x: usize, y: usize) -> bool {
    if (!domain.periodic_y && (y == 0 || y == domain.ny()))
        || (!domain.periodic_x && (x == 0 || x + 1 == domain.nx()))
    {
        return true;
    }
    let bottom = (y > 0).then(|| y - 1);
    let top = (y < domain.ny()).then_some(y);
    bottom.is_some_and(|cell| solid.get(x, cell) != 0)
        || top.is_some_and(|cell| solid.get(x, cell) != 0)
}

fn wrapped(index: isize, size: usize, periodic: bool) -> Option<usize> {
    if index >= 0 && index < size as isize {
        Some(index as usize)
    } else if periodic {
        Some(index.rem_euclid(size as isize) as usize)
    } else {
        None
    }
}

fn jacobi_face(
    source: &ScalarField2<f64>,
    current: &ScalarField2<f64>,
    output: &mut ScalarField2<f64>,
    logical: [usize; 2],
    periodic: [bool; 2],
    alpha: [f64; 2],
    fixed: impl Fn(usize, usize) -> bool,
) {
    for y in 0..logical[1] {
        for x in 0..logical[0] {
            if fixed(x, y) {
                output.set(x, y, current.get(x, y));
                continue;
            }
            let mut numerator = source.get(x, y);
            let mut denominator = 1.0;
            for (offset, weight) in [
                ([-1, 0], alpha[0]),
                ([1, 0], alpha[0]),
                ([0, -1], alpha[1]),
                ([0, 1], alpha[1]),
            ] {
                if let (Some(other_x), Some(other_y)) = (
                    wrapped(x as isize + offset[0], logical[0], periodic[0]),
                    wrapped(y as isize + offset[1], logical[1], periodic[1]),
                ) {
                    numerator += weight * current.get(other_x, other_y);
                    denominator += weight;
                }
            }
            output.set(x, y, numerator / denominator);
        }
    }
}

fn residual_face(
    source: &ScalarField2<f64>,
    value: &ScalarField2<f64>,
    logical: [usize; 2],
    periodic: [bool; 2],
    alpha: [f64; 2],
    fixed: impl Fn(usize, usize) -> bool,
) -> f64 {
    let mut maximum = 0.0_f64;
    for y in 0..logical[1] {
        for x in 0..logical[0] {
            if fixed(x, y) {
                continue;
            }
            let center = value.get(x, y);
            let mut operator = center;
            for (offset, weight) in [
                ([-1, 0], alpha[0]),
                ([1, 0], alpha[0]),
                ([0, -1], alpha[1]),
                ([0, 1], alpha[1]),
            ] {
                if let (Some(other_x), Some(other_y)) = (
                    wrapped(x as isize + offset[0], logical[0], periodic[0]),
                    wrapped(y as isize + offset[1], logical[1], periodic[1]),
                ) {
                    operator += weight * (center - value.get(other_x, other_y));
                }
            }
            maximum = maximum.max((operator - source.get(x, y)).abs());
        }
    }
    maximum
}

fn to_f64<T: FlowScalar>(field: &ScalarField2<T>) -> ScalarField2<f64> {
    ScalarField2::from_vec(
        field.nx(),
        field.ny(),
        field.values().iter().map(|value| value.to_f64()).collect(),
    )
    .expect("source field shape is internally consistent")
}

fn from_f64<T: FlowScalar>(field: &ScalarField2<f64>) -> ScalarField2<T> {
    ScalarField2::from_vec(
        field.nx(),
        field.ny(),
        field
            .values()
            .iter()
            .map(|value| T::from_f64(*value))
            .collect(),
    )
    .expect("diffused field shape is internally consistent")
}

/// Apply implicit viscosity transactionally.
///
/// # Errors
///
/// Returns a classified convergence error without modifying `grid`.
pub fn diffuse_mac<T: FlowScalar>(
    grid: &mut MacGrid2<T>,
    domain: GridDomain2,
    solid: &ScalarField2<u8>,
    wall_velocity: &VectorField2<T>,
    viscosity: f64,
    dt: f64,
    tolerance: f64,
    max_iterations: usize,
    freestream: [f64; 2],
    poiseuille: bool,
) -> Result<DiffusionReport, SolverError> {
    if !viscosity.is_finite()
        || viscosity < 0.0
        || !dt.is_finite()
        || dt <= 0.0
        || !tolerance.is_finite()
        || tolerance <= 0.0
        || max_iterations == 0
    {
        return Err(SolverError::new(
            FailureReason::ConvergenceFailure,
            FailureStage::Viscosity,
            "invalid viscosity solve parameters",
        ));
    }
    if viscosity == 0.0 {
        return Ok(DiffusionReport {
            converged: true,
            iterations: 0,
            residual_linf: 0.0,
        });
    }
    let source_u = to_f64(&grid.u);
    let source_v = to_f64(&grid.v);
    let mut current = grid.clone();
    let mut next = current.clone();
    let alpha = [
        viscosity * dt / domain.dx().powi(2),
        viscosity * dt / domain.dy().powi(2),
    ];
    let source_scale = source_u
        .values()
        .iter()
        .chain(source_v.values())
        .copied()
        .map(f64::abs)
        .fold(0.0, f64::max);
    let target = tolerance * source_scale.max(1.0);
    let u_logical = [
        if domain.periodic_x {
            domain.nx()
        } else {
            domain.nx() + 1
        },
        domain.ny(),
    ];
    let v_logical = [
        domain.nx(),
        if domain.periodic_y {
            domain.ny()
        } else {
            domain.ny() + 1
        },
    ];
    let mut residual_linf = f64::INFINITY;
    for iteration in 1..=max_iterations {
        let current_u = to_f64(&current.u);
        let current_v = to_f64(&current.v);
        let mut next_u = current_u.clone();
        let mut next_v = current_v.clone();
        jacobi_face(
            &source_u,
            &current_u,
            &mut next_u,
            u_logical,
            [domain.periodic_x, domain.periodic_y],
            alpha,
            |x, y| is_fixed_u(solid, domain, x, y),
        );
        jacobi_face(
            &source_v,
            &current_v,
            &mut next_v,
            v_logical,
            [domain.periodic_x, domain.periodic_y],
            alpha,
            |x, y| is_fixed_v(solid, domain, x, y),
        );
        next.u = from_f64(&next_u);
        next.v = from_f64(&next_v);
        apply_domain_boundaries(&mut next, domain, freestream, poiseuille);
        enforce_solid_faces(&mut next, solid, wall_velocity);
        let checked_u = to_f64(&next.u);
        let checked_v = to_f64(&next.v);
        residual_linf = residual_face(
            &source_u,
            &checked_u,
            u_logical,
            [domain.periodic_x, domain.periodic_y],
            alpha,
            |x, y| is_fixed_u(solid, domain, x, y),
        )
        .max(residual_face(
            &source_v,
            &checked_v,
            v_logical,
            [domain.periodic_x, domain.periodic_y],
            alpha,
            |x, y| is_fixed_v(solid, domain, x, y),
        ));
        if residual_linf <= target {
            *grid = next;
            return Ok(DiffusionReport {
                converged: true,
                iterations: iteration,
                residual_linf,
            });
        }
        std::mem::swap(&mut current, &mut next);
    }
    Err(SolverError::new(
        FailureReason::ConvergenceFailure,
        FailureStage::Viscosity,
        format!(
            "viscosity solve did not converge in {max_iterations} iterations (residual {residual_linf:.3e})"
        ),
    ))
}

#[cfg(test)]
mod tests {
    use super::diffuse_mac;
    use crate::{
        field::{MacGrid2, ScalarField2, VectorField2},
        grid::GridDomain2,
    };

    fn domain() -> GridDomain2 {
        GridDomain2 {
            bounds: [[0.0, 1.0], [0.0, 1.0]],
            resolution: [16, 12],
            periodic_x: true,
            periodic_y: true,
        }
    }

    #[test]
    fn viscosity_reduces_a_periodic_mode_in_both_precisions() {
        for use_f32 in [false, true] {
            if use_f32 {
                let mut grid = MacGrid2::filled(16, 12, [0.0_f32, 0.0]);
                for x in 0..16 {
                    grid.u.set(
                        x,
                        3,
                        (2.0 * std::f64::consts::PI * x as f64 / 16.0).sin() as f32,
                    );
                }
                grid.u.set(16, 3, grid.u.get(0, 3));
                let original = grid
                    .u
                    .values()
                    .iter()
                    .copied()
                    .map(f32::abs)
                    .fold(0.0, f32::max);
                let report = diffuse_mac(
                    &mut grid,
                    domain(),
                    &ScalarField2::filled(16, 12, 0),
                    &VectorField2::filled(16, 12, [0.0, 0.0]),
                    0.02,
                    0.01,
                    1.0e-5,
                    500,
                    [0.0, 0.0],
                    false,
                )
                .unwrap();
                assert!(report.converged);
                assert!(
                    grid.u
                        .values()
                        .iter()
                        .copied()
                        .map(f32::abs)
                        .fold(0.0, f32::max)
                        < original
                );
            } else {
                let mut grid = MacGrid2::filled(16, 12, [0.0_f64, 0.0]);
                for x in 0..16 {
                    grid.u
                        .set(x, 3, (2.0 * std::f64::consts::PI * x as f64 / 16.0).sin());
                }
                grid.u.set(16, 3, grid.u.get(0, 3));
                let original = grid
                    .u
                    .values()
                    .iter()
                    .copied()
                    .map(f64::abs)
                    .fold(0.0, f64::max);
                diffuse_mac(
                    &mut grid,
                    domain(),
                    &ScalarField2::filled(16, 12, 0),
                    &VectorField2::filled(16, 12, [0.0, 0.0]),
                    0.02,
                    0.01,
                    1.0e-8,
                    500,
                    [0.0, 0.0],
                    false,
                )
                .unwrap();
                assert!(
                    grid.u
                        .values()
                        .iter()
                        .copied()
                        .map(f64::abs)
                        .fold(0.0, f64::max)
                        < original
                );
            }
        }
    }

    #[test]
    fn nonconvergence_is_transactional() {
        let mut grid = MacGrid2::filled(16, 12, [0.0_f64, 0.0]);
        grid.u.set(4, 5, 3.0);
        let original = grid.clone();
        assert!(
            diffuse_mac(
                &mut grid,
                domain(),
                &ScalarField2::filled(16, 12, 0),
                &VectorField2::filled(16, 12, [0.0, 0.0]),
                10.0,
                1.0,
                1.0e-14,
                1,
                [0.0, 0.0],
                false
            )
            .is_err()
        );
        assert_eq!(grid, original);
    }
}
