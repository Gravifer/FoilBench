//! Deterministic matrix-free pressure projection for staggered grids.

#![allow(
    clippy::cast_possible_wrap,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::too_many_arguments,
    clippy::too_many_lines
)]

use crate::{
    field::{MacGrid2, ScalarField2},
    grid::{GridDomain2, apply_domain_boundaries, divergence},
    solver::{FailureReason, FailureStage, FlowScalar, SolverError},
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ProjectionReport {
    pub converged: bool,
    pub iterations: usize,
    pub residual_linf: f64,
    pub divergence_before_linf: f64,
    pub divergence_after_linf: f64,
}

fn fluid_linf<T: FlowScalar>(field: &ScalarField2<T>, solid: &ScalarField2<u8>) -> f64 {
    field
        .values()
        .iter()
        .zip(solid.values())
        .filter_map(|(value, is_solid)| (*is_solid == 0).then_some(value.to_f64().abs()))
        .fold(0.0, f64::max)
}

fn neighbor(x: usize, y: usize, offset: [isize; 2], domain: GridDomain2) -> Option<(usize, usize)> {
    let mut candidate_x = x as isize + offset[0];
    let mut candidate_y = y as isize + offset[1];
    if candidate_x < 0 || candidate_x >= domain.nx() as isize {
        if !domain.periodic_x {
            return None;
        }
        candidate_x = candidate_x.rem_euclid(domain.nx() as isize);
    }
    if candidate_y < 0 || candidate_y >= domain.ny() as isize {
        if !domain.periodic_y {
            return None;
        }
        candidate_y = candidate_y.rem_euclid(domain.ny() as isize);
    }
    Some((candidate_x as usize, candidate_y as usize))
}

fn pressure_operator(
    input: &[f64],
    output: &mut [f64],
    diagonal: &mut [f64],
    solid: &ScalarField2<u8>,
    domain: GridDomain2,
) {
    let wx = 1.0 / domain.dx().powi(2);
    let wy = 1.0 / domain.dy().powi(2);
    for y in 0..domain.ny() {
        for x in 0..domain.nx() {
            let index = y * domain.nx() + x;
            if solid.get(x, y) != 0 {
                output[index] = input[index];
                diagonal[index] = 1.0;
                continue;
            }
            let mut value = 0.0;
            let mut scale = 0.0;
            for (offset, weight) in [([-1, 0], wx), ([1, 0], wx), ([0, -1], wy), ([0, 1], wy)] {
                if let Some((other_x, other_y)) = neighbor(x, y, offset, domain)
                    && solid.get(other_x, other_y) == 0
                {
                    let other = other_y * domain.nx() + other_x;
                    value += weight * (input[index] - input[other]);
                    scale += weight;
                }
            }
            if scale == 0.0 {
                output[index] = input[index];
                diagonal[index] = 1.0;
            } else {
                output[index] = value;
                diagonal[index] = scale;
            }
        }
    }
}

fn solve_pressure(
    rhs: &[f64],
    solid: &ScalarField2<u8>,
    domain: GridDomain2,
    tolerance: f64,
    max_iterations: usize,
) -> (Vec<f64>, usize, f64, bool) {
    let count = rhs.len();
    let mut pressure = vec![0.0; count];
    let mut residual = rhs.to_vec();
    let mut operator = vec![0.0; count];
    let mut diagonal = vec![0.0; count];
    pressure_operator(&pressure, &mut operator, &mut diagonal, solid, domain);
    let fluid_count = solid
        .values()
        .iter()
        .fold(0_usize, |count, value| count + usize::from(*value == 0));
    if fluid_count > 0 {
        let mean = residual
            .iter()
            .zip(solid.values())
            .filter_map(|(value, is_solid)| (*is_solid == 0).then_some(*value))
            .sum::<f64>()
            / fluid_count as f64;
        for (value, is_solid) in residual.iter_mut().zip(solid.values()) {
            *value = if *is_solid == 0 { *value - mean } else { 0.0 };
        }
    }
    let rhs_scale = residual.iter().copied().map(f64::abs).fold(0.0, f64::max);
    let target = tolerance * rhs_scale.max(1.0);
    let mut residual_linf = rhs_scale;
    if residual_linf <= target {
        return (pressure, 0, residual_linf, true);
    }
    let mut preconditioned = vec![0.0; count];
    let mut direction = vec![0.0; count];
    for index in 0..count {
        preconditioned[index] = residual[index] / diagonal[index];
        direction[index] = preconditioned[index];
    }
    let mut rho = residual
        .iter()
        .zip(&preconditioned)
        .map(|(left, right)| left * right)
        .sum::<f64>();
    for iteration in 1..=max_iterations {
        pressure_operator(&direction, &mut operator, &mut diagonal, solid, domain);
        let denominator = direction
            .iter()
            .zip(&operator)
            .map(|(left, right)| left * right)
            .sum::<f64>();
        if !denominator.is_finite() || denominator.abs() <= f64::EPSILON || !rho.is_finite() {
            return (pressure, iteration, residual_linf, false);
        }
        let alpha = rho / denominator;
        for index in 0..count {
            pressure[index] += alpha * direction[index];
            residual[index] -= alpha * operator[index];
        }
        residual_linf = residual.iter().copied().map(f64::abs).fold(0.0, f64::max);
        if residual_linf <= target {
            return (pressure, iteration, residual_linf, true);
        }
        for index in 0..count {
            preconditioned[index] = residual[index] / diagonal[index];
        }
        let next_rho = residual
            .iter()
            .zip(&preconditioned)
            .map(|(left, right)| left * right)
            .sum::<f64>();
        if !next_rho.is_finite() || rho.abs() <= f64::EPSILON {
            return (pressure, iteration, residual_linf, false);
        }
        let beta = next_rho / rho;
        for index in 0..count {
            direction[index] = preconditioned[index] + beta * direction[index];
        }
        rho = next_rho;
    }
    (pressure, max_iterations, residual_linf, false)
}

/// Project a MAC velocity without modifying it when the pressure solve fails.
///
/// # Errors
///
/// Returns a classified error for invalid parameters or pressure nonconvergence.
pub fn project_incompressible<T: FlowScalar>(
    grid: &mut MacGrid2<T>,
    domain: GridDomain2,
    solid: &ScalarField2<u8>,
    dt: f64,
    tolerance: f64,
    max_iterations: usize,
    freestream: [f64; 2],
    poiseuille: bool,
) -> Result<ProjectionReport, SolverError> {
    if !dt.is_finite()
        || dt <= 0.0
        || !tolerance.is_finite()
        || tolerance <= 0.0
        || max_iterations == 0
    {
        return Err(SolverError::new(
            FailureReason::TimeContractFailure,
            FailureStage::Projection,
            "invalid projection interval, tolerance, or iteration limit",
        ));
    }
    if (grid.nx(), grid.ny()) != (domain.nx(), domain.ny())
        || (solid.nx(), solid.ny()) != (domain.nx(), domain.ny())
    {
        return Err(SolverError::new(
            FailureReason::ProjectionFailure,
            FailureStage::Projection,
            "projection field shape disagrees with the domain",
        ));
    }
    let initial_divergence = divergence(grid, domain);
    let before = fluid_linf(&initial_divergence, solid);
    let rhs = initial_divergence
        .values()
        .iter()
        .zip(solid.values())
        .map(|(value, is_solid)| {
            if *is_solid == 0 {
                -value.to_f64() / dt
            } else {
                0.0
            }
        })
        .collect::<Vec<_>>();
    let (pressure, iterations, residual_linf, converged) =
        solve_pressure(&rhs, solid, domain, tolerance, max_iterations);
    if !converged {
        return Err(SolverError::new(
            FailureReason::ProjectionFailure,
            FailureStage::Projection,
            format!("pressure solve did not converge in {iterations} iterations"),
        ));
    }

    let mut projected = grid.clone();
    for y in 0..domain.ny() {
        for x in 1..domain.nx() {
            if solid.get(x - 1, y) == 0 && solid.get(x, y) == 0 {
                let gradient = (pressure[y * domain.nx() + x] - pressure[y * domain.nx() + x - 1])
                    / domain.dx();
                projected.u.set(
                    x,
                    y,
                    T::from_f64(projected.u.get(x, y).to_f64() - dt * gradient),
                );
            }
        }
    }
    if domain.periodic_x {
        for y in 0..domain.ny() {
            if solid.get(domain.nx() - 1, y) == 0 && solid.get(0, y) == 0 {
                let gradient = (pressure[y * domain.nx()]
                    - pressure[y * domain.nx() + domain.nx() - 1])
                    / domain.dx();
                let value = T::from_f64(projected.u.get(0, y).to_f64() - dt * gradient);
                projected.u.set(0, y, value);
                projected.u.set(domain.nx(), y, value);
            }
        }
    }
    for y in 1..domain.ny() {
        for x in 0..domain.nx() {
            if solid.get(x, y - 1) == 0 && solid.get(x, y) == 0 {
                let gradient = (pressure[y * domain.nx() + x]
                    - pressure[(y - 1) * domain.nx() + x])
                    / domain.dy();
                projected.v.set(
                    x,
                    y,
                    T::from_f64(projected.v.get(x, y).to_f64() - dt * gradient),
                );
            }
        }
    }
    if domain.periodic_y {
        for x in 0..domain.nx() {
            if solid.get(x, domain.ny() - 1) == 0 && solid.get(x, 0) == 0 {
                let gradient =
                    (pressure[x] - pressure[(domain.ny() - 1) * domain.nx() + x]) / domain.dy();
                let value = T::from_f64(projected.v.get(x, 0).to_f64() - dt * gradient);
                projected.v.set(x, 0, value);
                projected.v.set(x, domain.ny(), value);
            }
        }
    }
    apply_domain_boundaries(&mut projected, domain, freestream, poiseuille);
    if !projected.is_finite() {
        return Err(SolverError::new(
            FailureReason::NonfiniteState,
            FailureStage::Projection,
            "pressure projection produced non-finite velocity",
        ));
    }
    let after = fluid_linf(&divergence(&projected, domain), solid);
    *grid = projected;
    Ok(ProjectionReport {
        converged,
        iterations,
        residual_linf,
        divergence_before_linf: before,
        divergence_after_linf: after,
    })
}

#[cfg(test)]
mod tests {
    use super::project_incompressible;
    use crate::{field::MacGrid2, field::ScalarField2, grid::GridDomain2};

    #[test]
    fn periodic_projection_reduces_divergence() {
        let domain = GridDomain2 {
            bounds: [[0.0, 1.0], [0.0, 1.0]],
            resolution: [24, 20],
            periodic_x: true,
            periodic_y: true,
        };
        let mut velocity = MacGrid2::filled(24, 20, [0.0_f64, 0.0]);
        for y in 0..20 {
            for x in 0..24 {
                velocity
                    .u
                    .set(x, y, (2.0 * std::f64::consts::PI * x as f64 / 24.0).sin());
            }
            velocity.u.set(24, y, velocity.u.get(0, y));
        }
        let solid = ScalarField2::filled(24, 20, 0_u8);
        let report = project_incompressible(
            &mut velocity,
            domain,
            &solid,
            0.02,
            1.0e-8,
            400,
            [0.0, 0.0],
            false,
        )
        .unwrap();
        assert!(report.divergence_after_linf < 1.0e-5 * report.divergence_before_linf);
    }

    #[test]
    fn failed_projection_does_not_modify_velocity() {
        let domain = GridDomain2 {
            bounds: [[0.0, 1.0], [0.0, 1.0]],
            resolution: [12, 10],
            periodic_x: true,
            periodic_y: true,
        };
        let mut velocity = MacGrid2::filled(12, 10, [0.0_f32, 0.0]);
        velocity.u.set(3, 4, 1.0);
        let original = velocity.clone();
        let solid = ScalarField2::filled(12, 10, 0_u8);
        assert!(
            project_incompressible(
                &mut velocity,
                domain,
                &solid,
                0.02,
                1.0e-12,
                1,
                [0.0, 0.0],
                false,
            )
            .is_err()
        );
        assert_eq!(velocity, original);
    }
}
