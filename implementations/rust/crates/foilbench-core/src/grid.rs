//! Two-dimensional staggered-grid geometry and interpolation.

#![allow(
    clippy::cast_possible_truncation,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss,
    clippy::missing_panics_doc
)]

use crate::{
    field::{MacGrid2, ScalarField2, VectorField2},
    scenario::Scenario,
    solver::FlowScalar,
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct GridDomain2 {
    pub bounds: [[f64; 2]; 2],
    pub resolution: [usize; 2],
    pub periodic_x: bool,
    pub periodic_y: bool,
}

impl GridDomain2 {
    /// Extract a checked two-dimensional grid domain.
    ///
    /// # Errors
    ///
    /// Returns an error when the scenario is not two-dimensional.
    pub fn from_scenario(scenario: &Scenario) -> Result<Self, &'static str> {
        if scenario.dimension() != 2 {
            return Err("two-dimensional grid requires a 2D scenario");
        }
        Ok(Self {
            bounds: [scenario.bounds()[0], scenario.bounds()[1]],
            resolution: [scenario.resolution()[0], scenario.resolution()[1]],
            periodic_x: scenario.periodic_axes().iter().any(|axis| axis == "x"),
            periodic_y: scenario.periodic_axes().iter().any(|axis| axis == "y"),
        })
    }

    #[must_use]
    pub fn nx(self) -> usize {
        self.resolution[0]
    }

    #[must_use]
    pub fn ny(self) -> usize {
        self.resolution[1]
    }

    #[must_use]
    pub fn dx(self) -> f64 {
        (self.bounds[0][1] - self.bounds[0][0]) / self.nx() as f64
    }

    #[must_use]
    pub fn dy(self) -> f64 {
        (self.bounds[1][1] - self.bounds[1][0]) / self.ny() as f64
    }

    #[must_use]
    pub fn cell_center(self, x: usize, y: usize) -> [f64; 2] {
        [
            self.bounds[0][0] + (x as f64 + 0.5) * self.dx(),
            self.bounds[1][0] + (y as f64 + 0.5) * self.dy(),
        ]
    }
}

#[must_use]
pub fn faces_to_cells<T: FlowScalar>(faces: &MacGrid2<T>) -> VectorField2<T> {
    let mut output =
        VectorField2::filled(faces.nx(), faces.ny(), [T::from_f64(0.0), T::from_f64(0.0)]);
    for y in 0..faces.ny() {
        for x in 0..faces.nx() {
            output.set(
                x,
                y,
                [
                    T::from_f64(
                        0.5 * (faces.u.get(x, y).to_f64() + faces.u.get(x + 1, y).to_f64()),
                    ),
                    T::from_f64(
                        0.5 * (faces.v.get(x, y).to_f64() + faces.v.get(x, y + 1).to_f64()),
                    ),
                ],
            );
        }
    }
    output
}

#[must_use]
pub fn cells_to_faces<T: FlowScalar>(
    cells: &VectorField2<T>,
    periodic_x: bool,
    periodic_y: bool,
) -> MacGrid2<T> {
    let nx = cells.nx();
    let ny = cells.ny();
    let mut output = MacGrid2::filled(nx, ny, [T::from_f64(0.0), T::from_f64(0.0)]);
    for y in 0..ny {
        for x in 0..=nx {
            let left = if periodic_x {
                (x + nx - 1) % nx
            } else {
                x.saturating_sub(1)
            };
            let right = if periodic_x { x % nx } else { x.min(nx - 1) };
            output.u.set(
                x,
                y,
                T::from_f64(
                    0.5 * (cells.get(left, y)[0].to_f64() + cells.get(right, y)[0].to_f64()),
                ),
            );
        }
    }
    for y in 0..=ny {
        for x in 0..nx {
            let bottom = if periodic_y {
                (y + ny - 1) % ny
            } else {
                y.saturating_sub(1)
            };
            let top = if periodic_y { y % ny } else { y.min(ny - 1) };
            output.v.set(
                x,
                y,
                T::from_f64(
                    0.5 * (cells.get(x, bottom)[1].to_f64() + cells.get(x, top)[1].to_f64()),
                ),
            );
        }
    }
    output
}

pub fn apply_domain_boundaries<T: FlowScalar>(
    grid: &mut MacGrid2<T>,
    domain: GridDomain2,
    freestream: [f64; 2],
    channel_walls: bool,
) {
    let nx = domain.nx();
    let ny = domain.ny();
    if domain.periodic_x {
        for y in 0..ny {
            let value = T::from_f64(0.5 * (grid.u.get(0, y).to_f64() + grid.u.get(nx, y).to_f64()));
            grid.u.set(0, y, value);
            grid.u.set(nx, y, value);
        }
    } else {
        for y in 0..ny {
            grid.u.set(0, y, T::from_f64(freestream[0]));
            grid.u.set(nx, y, grid.u.get(nx - 1, y));
        }
        for y in 0..=ny {
            grid.v.set(0, y, T::from_f64(freestream[1]));
            grid.v.set(nx - 1, y, grid.v.get(nx - 2, y));
        }
    }
    if domain.periodic_y {
        for x in 0..nx {
            let value = T::from_f64(0.5 * (grid.v.get(x, 0).to_f64() + grid.v.get(x, ny).to_f64()));
            grid.v.set(x, 0, value);
            grid.v.set(x, ny, value);
        }
    } else if channel_walls {
        for x in 0..nx {
            grid.v.set(x, 0, T::from_f64(0.0));
            grid.v.set(x, ny, T::from_f64(0.0));
        }
        for x in 0..=nx {
            grid.u.set(x, 0, T::from_f64(0.0));
            grid.u.set(x, ny - 1, T::from_f64(0.0));
        }
    } else {
        for x in 0..nx {
            grid.v.set(x, 0, T::from_f64(freestream[1]));
            grid.v.set(x, ny, T::from_f64(freestream[1]));
        }
        for x in 0..=nx {
            grid.u.set(x, 0, T::from_f64(freestream[0]));
            grid.u.set(x, ny - 1, T::from_f64(freestream[0]));
        }
    }
}

pub fn enforce_solid_faces<T: FlowScalar>(
    grid: &mut MacGrid2<T>,
    solid: &ScalarField2<u8>,
    wall: &VectorField2<T>,
) {
    let nx = grid.nx();
    let ny = grid.ny();
    assert_eq!((solid.nx(), solid.ny()), (nx, ny));
    assert_eq!((wall.nx(), wall.ny()), (nx, ny));
    for y in 0..ny {
        for x in 0..=nx {
            let left = x.checked_sub(1).filter(|&value| value < nx);
            let right = (x < nx).then_some(x);
            let left_solid = left.is_some_and(|cell| solid.get(cell, y) != 0);
            let right_solid = right.is_some_and(|cell| solid.get(cell, y) != 0);
            if left_solid || right_solid {
                let mut sum = 0.0;
                let mut count = 0.0;
                for cell in [left, right].into_iter().flatten() {
                    if solid.get(cell, y) != 0 {
                        sum += wall.get(cell, y)[0].to_f64();
                        count += 1.0;
                    }
                }
                grid.u.set(x, y, T::from_f64(sum / count));
            }
        }
    }
    for y in 0..=ny {
        for x in 0..nx {
            let bottom = y.checked_sub(1).filter(|&value| value < ny);
            let top = (y < ny).then_some(y);
            let bottom_solid = bottom.is_some_and(|cell| solid.get(x, cell) != 0);
            let top_solid = top.is_some_and(|cell| solid.get(x, cell) != 0);
            if bottom_solid || top_solid {
                let mut sum = 0.0;
                let mut count = 0.0;
                for cell in [bottom, top].into_iter().flatten() {
                    if solid.get(x, cell) != 0 {
                        sum += wall.get(x, cell)[1].to_f64();
                        count += 1.0;
                    }
                }
                grid.v.set(x, y, T::from_f64(sum / count));
            }
        }
    }
}

#[must_use]
pub fn divergence<T: FlowScalar>(grid: &MacGrid2<T>, domain: GridDomain2) -> ScalarField2<T> {
    let mut output = ScalarField2::filled(grid.nx(), grid.ny(), T::from_f64(0.0));
    for y in 0..grid.ny() {
        for x in 0..grid.nx() {
            let value = (grid.u.get(x + 1, y).to_f64() - grid.u.get(x, y).to_f64()) / domain.dx()
                + (grid.v.get(x, y + 1).to_f64() - grid.v.get(x, y).to_f64()) / domain.dy();
            output.set(x, y, T::from_f64(value));
        }
    }
    output
}

fn wrapped_coordinate(value: f64, count: usize) -> f64 {
    value.rem_euclid(count as f64)
}

#[must_use]
pub fn sample_cells<T: FlowScalar>(
    field: &VectorField2<T>,
    domain: GridDomain2,
    point: [f64; 2],
) -> [T; 2] {
    let raw_x = (point[0] - domain.bounds[0][0]) / domain.dx() - 0.5;
    let raw_y = (point[1] - domain.bounds[1][0]) / domain.dy() - 0.5;
    let gx = if domain.periodic_x {
        wrapped_coordinate(raw_x, domain.nx())
    } else {
        raw_x.clamp(0.0, (domain.nx() - 1) as f64)
    };
    let gy = if domain.periodic_y {
        wrapped_coordinate(raw_y, domain.ny())
    } else {
        raw_y.clamp(0.0, (domain.ny() - 1) as f64)
    };
    let x0 = gx.floor() as usize;
    let y0 = gy.floor() as usize;
    let x1 = if domain.periodic_x {
        (x0 + 1) % domain.nx()
    } else {
        (x0 + 1).min(domain.nx() - 1)
    };
    let y1 = if domain.periodic_y {
        (y0 + 1) % domain.ny()
    } else {
        (y0 + 1).min(domain.ny() - 1)
    };
    let tx = gx - x0 as f64;
    let ty = gy - y0 as f64;
    let mut output = [T::from_f64(0.0), T::from_f64(0.0)];
    for (component, output_component) in output.iter_mut().enumerate() {
        let bottom = (1.0 - tx) * field.get(x0, y0)[component].to_f64()
            + tx * field.get(x1, y0)[component].to_f64();
        let top = (1.0 - tx) * field.get(x0, y1)[component].to_f64()
            + tx * field.get(x1, y1)[component].to_f64();
        *output_component = T::from_f64((1.0 - ty) * bottom + ty * top);
    }
    output
}

#[must_use]
pub fn rk2_backtrace<T: FlowScalar>(
    velocity: &VectorField2<T>,
    domain: GridDomain2,
    point: [f64; 2],
    dt: f64,
) -> [f64; 2] {
    let first = sample_cells(velocity, domain, point);
    let midpoint = [
        point[0] - 0.5 * dt * first[0].to_f64(),
        point[1] - 0.5 * dt * first[1].to_f64(),
    ];
    let second = sample_cells(velocity, domain, midpoint);
    [
        point[0] - dt * second[0].to_f64(),
        point[1] - dt * second[1].to_f64(),
    ]
}

#[cfg(test)]
mod tests {
    #![allow(clippy::float_cmp)]
    use super::{
        GridDomain2, apply_domain_boundaries, cells_to_faces, divergence, faces_to_cells,
        rk2_backtrace, sample_cells,
    };
    use crate::field::{MacGrid2, VectorField2};

    fn domain(periodic_x: bool, periodic_y: bool) -> GridDomain2 {
        GridDomain2 {
            bounds: [[0.0, 4.0], [0.0, 3.0]],
            resolution: [4, 3],
            periodic_x,
            periodic_y,
        }
    }

    #[test]
    fn cell_face_roundtrip_preserves_uniform_velocity() {
        let cells = VectorField2::filled(4, 3, [2.0_f64, -0.5]);
        let faces = cells_to_faces(&cells, false, false);
        assert_eq!(faces_to_cells(&faces), cells);
        assert!(
            divergence(&faces, domain(false, false))
                .values()
                .iter()
                .all(|value| value.abs() < 1.0e-12)
        );
    }

    #[test]
    fn boundaries_match_revision5_mac_mapping() {
        let mut faces = MacGrid2::filled(4, 3, [-3.0_f64, -4.0]);
        apply_domain_boundaries(&mut faces, domain(false, false), [1.25, -0.5], false);
        assert!((faces.u.get(0, 1) - 1.25).abs() < 1.0e-12);
        assert!((faces.u.get(2, 0) - 1.25).abs() < 1.0e-12);
        assert!((faces.v.get(2, 0) + 0.5).abs() < 1.0e-12);
    }

    #[test]
    fn periodic_bilinear_sampling_and_rk2_are_finite() {
        let velocity = VectorField2::filled(4, 3, [1.0_f32, 0.5]);
        let selected = domain(true, true);
        assert_eq!(sample_cells(&velocity, selected, [4.25, -0.25]), [1.0, 0.5]);
        let backtraced = rk2_backtrace(&velocity, selected, [1.0, 1.0], 0.2);
        assert!((backtraced[0] - 0.8).abs() < 1.0e-6);
        assert!((backtraced[1] - 0.9).abs() < 1.0e-6);
    }
}
