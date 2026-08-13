//! Checked, flat x-major field storage shared by every Rust solver.

#![allow(clippy::missing_panics_doc)]

use crate::solver::FlowScalar;

#[derive(Clone, Debug, PartialEq)]
pub struct ScalarField2<T: Copy> {
    nx: usize,
    ny: usize,
    values: Vec<T>,
}

impl<T: Copy> ScalarField2<T> {
    #[must_use]
    pub fn filled(nx: usize, ny: usize, value: T) -> Self {
        Self {
            nx,
            ny,
            values: vec![value; nx.saturating_mul(ny)],
        }
    }

    /// Construct a checked field from x-major values.
    ///
    /// # Errors
    ///
    /// Returns an error when the payload length differs from `nx * ny`.
    pub fn from_vec(nx: usize, ny: usize, values: Vec<T>) -> Result<Self, &'static str> {
        if values.len() != nx.saturating_mul(ny) {
            return Err("scalar field payload length disagrees with its shape");
        }
        Ok(Self { nx, ny, values })
    }

    #[must_use]
    pub fn nx(&self) -> usize {
        self.nx
    }

    #[must_use]
    pub fn ny(&self) -> usize {
        self.ny
    }

    #[must_use]
    pub fn index(&self, x: usize, y: usize) -> usize {
        assert!(
            x < self.nx && y < self.ny,
            "scalar field index out of bounds"
        );
        y * self.nx + x
    }

    #[must_use]
    pub fn get(&self, x: usize, y: usize) -> T {
        self.values[self.index(x, y)]
    }

    pub fn set(&mut self, x: usize, y: usize, value: T) {
        let index = self.index(x, y);
        self.values[index] = value;
    }

    #[must_use]
    pub fn values(&self) -> &[T] {
        &self.values
    }

    pub fn values_mut(&mut self) -> &mut [T] {
        &mut self.values
    }
}

impl<T: FlowScalar> ScalarField2<T> {
    #[must_use]
    pub fn is_finite(&self) -> bool {
        self.values.iter().copied().all(FlowScalar::is_finite)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct VectorField2<T: FlowScalar> {
    nx: usize,
    ny: usize,
    values: Vec<[T; 2]>,
}

impl<T: FlowScalar> VectorField2<T> {
    #[must_use]
    pub fn filled(nx: usize, ny: usize, value: [T; 2]) -> Self {
        Self {
            nx,
            ny,
            values: vec![value; nx.saturating_mul(ny)],
        }
    }

    /// Construct a checked vector field from x-major values.
    ///
    /// # Errors
    ///
    /// Returns an error when the payload length differs from `nx * ny`.
    pub fn from_vec(nx: usize, ny: usize, values: Vec<[T; 2]>) -> Result<Self, &'static str> {
        if values.len() != nx.saturating_mul(ny) {
            return Err("vector field payload length disagrees with its shape");
        }
        Ok(Self { nx, ny, values })
    }

    #[must_use]
    pub fn nx(&self) -> usize {
        self.nx
    }

    #[must_use]
    pub fn ny(&self) -> usize {
        self.ny
    }

    #[must_use]
    pub fn index(&self, x: usize, y: usize) -> usize {
        assert!(
            x < self.nx && y < self.ny,
            "vector field index out of bounds"
        );
        y * self.nx + x
    }

    #[must_use]
    pub fn get(&self, x: usize, y: usize) -> [T; 2] {
        self.values[self.index(x, y)]
    }

    pub fn set(&mut self, x: usize, y: usize, value: [T; 2]) {
        let index = self.index(x, y);
        self.values[index] = value;
    }

    #[must_use]
    pub fn values(&self) -> &[[T; 2]] {
        &self.values
    }

    pub fn values_mut(&mut self) -> &mut [[T; 2]] {
        &mut self.values
    }

    #[must_use]
    pub fn is_finite(&self) -> bool {
        self.values
            .iter()
            .flatten()
            .copied()
            .all(FlowScalar::is_finite)
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct MacGrid2<T: FlowScalar> {
    nx: usize,
    ny: usize,
    pub u: ScalarField2<T>,
    pub v: ScalarField2<T>,
}

impl<T: FlowScalar> MacGrid2<T> {
    #[must_use]
    pub fn filled(nx: usize, ny: usize, velocity: [T; 2]) -> Self {
        Self {
            nx,
            ny,
            u: ScalarField2::filled(nx + 1, ny, velocity[0]),
            v: ScalarField2::filled(nx, ny + 1, velocity[1]),
        }
    }

    #[must_use]
    pub fn nx(&self) -> usize {
        self.nx
    }

    #[must_use]
    pub fn ny(&self) -> usize {
        self.ny
    }

    #[must_use]
    pub fn is_finite(&self) -> bool {
        self.u.is_finite() && self.v.is_finite()
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::float_cmp)]
    use super::{MacGrid2, ScalarField2, VectorField2};

    #[test]
    fn x_major_fields_have_checked_shapes() {
        let mut scalar = ScalarField2::filled(4, 3, 0.0_f32);
        scalar.set(2, 1, 7.0);
        assert_eq!(scalar.values()[6], 7.0);
        assert!(ScalarField2::<f32>::from_vec(4, 3, vec![0.0; 11]).is_err());

        let vector = VectorField2::filled(4, 3, [1.0_f64, -1.0]);
        assert_eq!(vector.values().len(), 12);
        assert!(vector.is_finite());

        let mac = MacGrid2::filled(4, 3, [1.0_f32, 0.0]);
        assert_eq!((mac.u.nx(), mac.u.ny()), (5, 3));
        assert_eq!((mac.v.nx(), mac.v.ny()), (4, 4));
    }
}
