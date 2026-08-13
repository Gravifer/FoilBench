//! Authoritative-pose foil fields on the shared cell-centered grid.

use crate::{
    field::{ScalarField2, VectorField2},
    geometry::NacaFoil,
    grid::GridDomain2,
    scenario::ControlState,
    solver::FlowScalar,
};

#[derive(Clone, Debug, PartialEq)]
pub struct GeometryFields2<T: FlowScalar> {
    pub signed_distance: ScalarField2<T>,
    pub solid: ScalarField2<u8>,
    pub normals: VectorField2<T>,
    pub wall_velocity: VectorField2<T>,
}

#[must_use]
pub fn rasterize_geometry<T: FlowScalar>(
    foil: &NacaFoil,
    domain: GridDomain2,
    control: ControlState,
) -> GeometryFields2<T> {
    let zero = T::from_f64(0.0);
    let mut signed_distance = ScalarField2::filled(domain.nx(), domain.ny(), zero);
    let mut solid = ScalarField2::filled(domain.nx(), domain.ny(), 0_u8);
    let mut normals = VectorField2::filled(domain.nx(), domain.ny(), [zero, zero]);
    let mut wall_velocity = VectorField2::filled(domain.nx(), domain.ny(), [zero, zero]);
    for y in 0..domain.ny() {
        for x in 0..domain.nx() {
            let point = domain.cell_center(x, y);
            let distance = foil.signed_distance(point, control.angle_degrees);
            signed_distance.set(x, y, T::from_f64(distance));
            solid.set(x, y, u8::from(distance <= 0.0));
            let normal = foil.normal(point, control.angle_degrees);
            normals.set(x, y, [T::from_f64(normal[0]), T::from_f64(normal[1])]);
            let velocity = foil.wall_velocity(point, control.angular_velocity_degrees);
            wall_velocity.set(x, y, [T::from_f64(velocity[0]), T::from_f64(velocity[1])]);
        }
    }
    GeometryFields2 {
        signed_distance,
        solid,
        normals,
        wall_velocity,
    }
}

#[cfg(test)]
mod tests {
    use super::rasterize_geometry;
    use crate::{
        geometry::{FoilDescriptor, NacaFoil},
        grid::GridDomain2,
        scenario::ControlState,
    };

    #[test]
    fn raster_uses_current_pose_and_wall_motion() {
        let foil = NacaFoil::new(FoilDescriptor {
            family: "naca-four-digit-v1".into(),
            naca: "0012".into(),
            chord: 1.0,
            pivot: vec![0.0, 0.0],
        })
        .unwrap();
        let domain = GridDomain2 {
            bounds: [[-0.5, 1.5], [-0.5, 0.5]],
            resolution: [32, 16],
            periodic_x: false,
            periodic_y: false,
        };
        let fields = rasterize_geometry::<f64>(
            &foil,
            domain,
            ControlState {
                time: 2.0,
                angle_degrees: 20.0,
                angular_velocity_degrees: 30.0,
            },
        );
        assert!(fields.solid.values().iter().any(|value| *value != 0));
        assert!(
            fields
                .wall_velocity
                .values()
                .iter()
                .any(|value| { value[0].abs() > 0.0 || value[1].abs() > 0.0 })
        );
        assert!(fields.signed_distance.is_finite());
        assert!(fields.normals.is_finite());
    }
}
