use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FoilDescriptor {
    #[serde(default = "default_family")]
    pub family: String,
    pub naca: String,
    pub chord: f64,
    pub pivot: Vec<f64>,
}

fn default_family() -> String {
    "naca-four-digit-v1".into()
}

#[derive(Clone, Debug)]
pub struct NacaFoil {
    descriptor: FoilDescriptor,
    camber: f64,
    camber_position: f64,
    thickness: f64,
}

impl NacaFoil {
    /// Construct the proposed Revision 5 NACA geometry.
    ///
    /// # Errors
    ///
    /// Returns an error for unsupported families or invalid descriptor fields.
    pub fn new(descriptor: FoilDescriptor) -> Result<Self, &'static str> {
        if descriptor.family != "naca-four-digit-v1" {
            return Err("unsupported geometry family");
        }
        if descriptor.naca.len() != 4
            || !descriptor.naca.bytes().all(|value| value.is_ascii_digit())
        {
            return Err("NACA code must contain four ASCII digits");
        }
        if !descriptor.chord.is_finite()
            || descriptor.chord <= 0.0
            || descriptor.pivot.len() < 2
            || !descriptor.pivot.iter().all(|value| value.is_finite())
        {
            return Err("invalid foil chord or pivot");
        }
        let digits = descriptor.naca.as_bytes();
        let camber = f64::from(digits[0] - b'0') / 100.0;
        let camber_position = f64::from(digits[1] - b'0') / 10.0;
        let thickness = f64::from((digits[2] - b'0') * 10 + digits[3] - b'0') / 100.0;
        Ok(Self {
            descriptor,
            camber,
            camber_position,
            thickness,
        })
    }

    #[must_use]
    pub fn descriptor(&self) -> &FoilDescriptor {
        &self.descriptor
    }

    #[must_use]
    pub fn maximum_radius(&self) -> f64 {
        (0.75 * self.descriptor.chord)
            .hypot((self.camber + 0.51 * self.thickness) * self.descriptor.chord)
    }

    #[must_use]
    pub fn surfaces(&self, x_local: f64) -> (f64, f64) {
        let q = (x_local / self.descriptor.chord).clamp(0.0, 1.0);
        let thickness = 5.0
            * self.thickness
            * self.descriptor.chord
            * (0.2969 * q.sqrt() - 0.1260 * q - 0.3516 * q.powi(2) + 0.2843 * q.powi(3)
                - 0.1036 * q.powi(4));
        let camber = if self.camber <= 0.0 || self.camber_position <= 0.0 {
            0.0
        } else if q < self.camber_position {
            self.camber / self.camber_position.powi(2)
                * (2.0 * self.camber_position * q - q.powi(2))
                * self.descriptor.chord
        } else {
            self.camber / (1.0 - self.camber_position).powi(2)
                * ((1.0 - 2.0 * self.camber_position) + 2.0 * self.camber_position * q - q.powi(2))
                * self.descriptor.chord
        };
        (camber + thickness, camber - thickness)
    }

    fn to_local(&self, point: [f64; 2], angle_degrees: f64) -> [f64; 2] {
        let angle = angle_degrees.to_radians();
        let dx = point[0] - self.descriptor.pivot[0];
        let dy = point[1] - self.descriptor.pivot[1];
        [
            angle.cos() * dx + angle.sin() * dy + 0.25 * self.descriptor.chord,
            -angle.sin() * dx + angle.cos() * dy,
        ]
    }

    #[must_use]
    pub fn signed_distance(&self, point: [f64; 2], angle_degrees: f64) -> f64 {
        let [x, y] = self.to_local(point, angle_degrees);
        let (upper, lower) = self.surfaces(x);
        let vertical_outside = (y - upper).max(lower - y);
        let vertical_inside = -(upper - y).min(y - lower);
        let vertical = if y <= upper && y >= lower {
            vertical_inside
        } else {
            vertical_outside
        };
        if x >= 0.0 && x <= self.descriptor.chord {
            return vertical;
        }
        (-x).max(x - self.descriptor.chord)
            .max(0.0)
            .hypot(vertical.max(0.0))
    }

    #[must_use]
    pub fn normal(&self, point: [f64; 2], angle_degrees: f64) -> [f64; 2] {
        let epsilon = (1.0e-4 * self.descriptor.chord).max(1.0e-6);
        let mut dx = self.signed_distance([point[0] + epsilon, point[1]], angle_degrees)
            - self.signed_distance([point[0] - epsilon, point[1]], angle_degrees);
        let mut dy = self.signed_distance([point[0], point[1] + epsilon], angle_degrees)
            - self.signed_distance([point[0], point[1] - epsilon], angle_degrees);
        let mut length = dx.hypot(dy);
        if length < epsilon {
            let angle = angle_degrees.to_radians();
            (dx, dy, length) = (-angle.sin(), angle.cos(), 1.0);
        }
        [dx / length.max(epsilon), dy / length.max(epsilon)]
    }

    #[must_use]
    pub fn wall_velocity(&self, point: [f64; 2], angular_velocity_degrees: f64) -> [f64; 2] {
        let omega = angular_velocity_degrees.to_radians();
        let dx = point[0] - self.descriptor.pivot[0];
        let dy = point[1] - self.descriptor.pivot[1];
        [-omega * dy, omega * dx]
    }
}

#[cfg(test)]
mod tests {
    use super::{FoilDescriptor, NacaFoil};
    use serde::Deserialize;

    fn foil() -> NacaFoil {
        NacaFoil::new(FoilDescriptor {
            family: "naca-four-digit-v1".into(),
            naca: "2412".into(),
            chord: 1.0,
            pivot: vec![0.1, -0.2],
        })
        .unwrap()
    }

    #[test]
    fn matches_shared_surface_samples() {
        let (upper, lower) = foil().surfaces(0.4);
        assert!((upper - 0.077_997_852_476_479_02).abs() < 1.0e-14);
        assert!((lower + 0.037_997_852_476_479_02).abs() < 1.0e-14);
    }

    #[test]
    fn matches_shared_signed_distance_sample() {
        assert!((foil().signed_distance([0.1, -0.2], 0.0) + 0.042_22).abs() < 1.0e-12);
    }

    #[derive(Deserialize)]
    struct Revision5GeometryFixture {
        descriptor: FoilDescriptor,
        surface_x: Vec<f64>,
        surface_upper: Vec<f64>,
        surface_lower: Vec<f64>,
        angle_degrees: f64,
        points: Vec<[f64; 2]>,
        signed_distance: Vec<f64>,
        normals: Vec<[f64; 2]>,
        angular_velocity_degrees: f64,
        wall_velocity: Vec<[f64; 2]>,
        maximum_radius: f64,
    }

    #[test]
    fn consumes_revision5_geometry_fixture() {
        let fixture: Revision5GeometryFixture = serde_json::from_str(include_str!(
            "../../../../../spec/proposals/revision5/fixtures/geometry-v1.json"
        ))
        .unwrap();
        let foil = NacaFoil::new(fixture.descriptor).unwrap();
        for ((x, expected_upper), expected_lower) in fixture
            .surface_x
            .iter()
            .zip(&fixture.surface_upper)
            .zip(&fixture.surface_lower)
        {
            let (upper, lower) = foil.surfaces(*x);
            assert!((upper - expected_upper).abs() < 1.0e-12);
            assert!((lower - expected_lower).abs() < 1.0e-12);
        }
        for (((point, expected_distance), expected_normal), expected_wall) in fixture
            .points
            .iter()
            .zip(&fixture.signed_distance)
            .zip(&fixture.normals)
            .zip(&fixture.wall_velocity)
        {
            assert!(
                (foil.signed_distance(*point, fixture.angle_degrees) - expected_distance).abs()
                    < 1.0e-10
            );
            let normal = foil.normal(*point, fixture.angle_degrees);
            assert!((normal[0] - expected_normal[0]).abs() < 2.0e-6);
            assert!((normal[1] - expected_normal[1]).abs() < 2.0e-6);
            let wall = foil.wall_velocity(*point, fixture.angular_velocity_degrees);
            assert!((wall[0] - expected_wall[0]).abs() < 1.0e-12);
            assert!((wall[1] - expected_wall[1]).abs() < 1.0e-12);
        }
        assert!((foil.maximum_radius() - fixture.maximum_radius).abs() < 1.0e-12);
    }
}
