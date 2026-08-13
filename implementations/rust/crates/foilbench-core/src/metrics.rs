//! Shared native MAC diagnostics and language-independent wake metrics.

#![allow(clippy::cast_precision_loss)]

use crate::{
    field::{MacGrid2, ScalarField2, VectorField2},
    grid::{GridDomain2, divergence, faces_to_cells},
    solver::FlowScalar,
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FlowMetrics {
    pub kinetic_energy: f64,
    pub enstrophy: f64,
    pub divergence_linf: f64,
    pub solid_leakage: f64,
    pub maximum_speed: f64,
    pub maximum_wall_speed: f64,
    pub wake_width: f64,
    pub recirculation_area: f64,
}

fn derivative<T: FlowScalar>(
    velocity: &VectorField2<T>,
    domain: GridDomain2,
    x: usize,
    y: usize,
    component: usize,
    axis: usize,
) -> f64 {
    let (size, spacing, periodic) = if axis == 0 {
        (domain.nx(), domain.dx(), domain.periodic_x)
    } else {
        (domain.ny(), domain.dy(), domain.periodic_y)
    };
    let index = if axis == 0 { x } else { y };
    let lower = if index > 0 {
        index - 1
    } else if periodic {
        size - 1
    } else {
        index
    };
    let upper = if index + 1 < size {
        index + 1
    } else if periodic {
        0
    } else {
        index
    };
    let lower_value = if axis == 0 {
        velocity.get(lower, y)[component]
    } else {
        velocity.get(x, lower)[component]
    };
    let upper_value = if axis == 0 {
        velocity.get(upper, y)[component]
    } else {
        velocity.get(x, upper)[component]
    };
    let divisor = if lower == upper {
        spacing
    } else if periodic || (index > 0 && index + 1 < size) {
        2.0 * spacing
    } else {
        spacing
    };
    (upper_value.to_f64() - lower_value.to_f64()) / divisor
}

fn leakage<T: FlowScalar>(
    grid: &MacGrid2<T>,
    solid: &ScalarField2<u8>,
    wall_velocity: &VectorField2<T>,
) -> f64 {
    let mut maximum = 0.0_f64;
    for y in 0..grid.ny() {
        for x in 1..grid.nx() {
            let left = solid.get(x - 1, y) != 0;
            let right = solid.get(x, y) != 0;
            if left != right {
                let cell = if left { x - 1 } else { x };
                maximum = maximum.max(
                    (grid.u.get(x, y).to_f64() - wall_velocity.get(cell, y)[0].to_f64()).abs(),
                );
            }
        }
    }
    for y in 1..grid.ny() {
        for x in 0..grid.nx() {
            let bottom = solid.get(x, y - 1) != 0;
            let top = solid.get(x, y) != 0;
            if bottom != top {
                let cell = if bottom { y - 1 } else { y };
                maximum = maximum.max(
                    (grid.v.get(x, y).to_f64() - wall_velocity.get(x, cell)[1].to_f64()).abs(),
                );
            }
        }
    }
    maximum
}

#[must_use]
pub fn compute_flow_metrics<T: FlowScalar>(
    grid: &MacGrid2<T>,
    domain: GridDomain2,
    solid: &ScalarField2<u8>,
    wall_velocity: &VectorField2<T>,
    freestream: [f64; 2],
    foil_pivot_x: f64,
    chord: f64,
) -> FlowMetrics {
    let velocity = faces_to_cells(grid);
    let native_divergence = divergence(grid, domain);
    let mut kinetic_sum = 0.0;
    let mut enstrophy_sum = 0.0;
    let mut fluid_count = 0_usize;
    let mut divergence_linf = 0.0_f64;
    let mut maximum_speed = 0.0_f64;
    let mut recirculation_cells = 0_usize;
    let mut wake_rows = vec![false; domain.ny()];
    for (y, wake_row) in wake_rows.iter_mut().enumerate().take(domain.ny()) {
        for x in 0..domain.nx() {
            if solid.get(x, y) != 0 {
                continue;
            }
            fluid_count += 1;
            let value = velocity.get(x, y);
            let ux = value[0].to_f64();
            let uy = value[1].to_f64();
            let speed2 = ux * ux + uy * uy;
            kinetic_sum += 0.5 * speed2;
            maximum_speed = maximum_speed.max(speed2.sqrt());
            let omega = derivative(&velocity, domain, x, y, 1, 0)
                - derivative(&velocity, domain, x, y, 0, 1);
            enstrophy_sum += 0.5 * omega * omega;
            divergence_linf = divergence_linf.max(native_divergence.get(x, y).to_f64().abs());
            let center_x = domain.cell_center(x, y)[0];
            if center_x > foil_pivot_x && ux < 0.0 {
                recirculation_cells += 1;
            }
            if center_x > foil_pivot_x + chord && freestream[0] - ux > 0.1 * freestream[0].abs() {
                *wake_row = true;
            }
        }
    }
    let divisor = fluid_count.max(1) as f64;
    let maximum_wall_speed = wall_velocity
        .values()
        .iter()
        .zip(solid.values())
        .filter_map(|(value, is_solid)| (*is_solid != 0).then_some(value))
        .map(|value| value[0].to_f64().hypot(value[1].to_f64()))
        .fold(0.0, f64::max);
    FlowMetrics {
        kinetic_energy: kinetic_sum / divisor,
        enstrophy: enstrophy_sum / divisor,
        divergence_linf,
        solid_leakage: leakage(grid, solid, wall_velocity),
        maximum_speed,
        maximum_wall_speed,
        wake_width: wake_rows.iter().filter(|value| **value).count() as f64 * domain.dy(),
        recirculation_area: recirculation_cells as f64 * domain.dx() * domain.dy(),
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::float_cmp)]
    use super::compute_flow_metrics;
    use crate::{
        field::{MacGrid2, ScalarField2, VectorField2},
        grid::GridDomain2,
    };

    #[test]
    fn uniform_flow_has_expected_energy_and_no_leakage() {
        let domain = GridDomain2 {
            bounds: [[0.0, 4.0], [-1.0, 1.0]],
            resolution: [8, 4],
            periodic_x: false,
            periodic_y: false,
        };
        let metrics = compute_flow_metrics(
            &MacGrid2::filled(8, 4, [1.0_f64, 0.0]),
            domain,
            &ScalarField2::filled(8, 4, 0_u8),
            &VectorField2::filled(8, 4, [0.0_f64, 0.0]),
            [1.0, 0.0],
            1.0,
            1.0,
        );
        assert!((metrics.kinetic_energy - 0.5).abs() < 1.0e-12);
        assert_eq!(metrics.enstrophy, 0.0);
        assert_eq!(metrics.divergence_linf, 0.0);
        assert_eq!(metrics.solid_leakage, 0.0);
    }
}
