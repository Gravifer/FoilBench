# MAC-grid domain boundary semantics

Status: proposed Revision 5 normative component.

Stable Fluids and PIC/FLIP share the same staggered MAC-grid domain treatment.
This contract fixes observable face values, not the internal pressure matrix or
linear-solver implementation.

For a nonperiodic x axis, lower-x is the prescribed inlet and upper-x is a
zero-gradient outlet. The inlet-normal u face equals freestream u; the outlet
u face copies its adjacent interior face. Tangential v values in the lower-x
guard column equal freestream v and those in the upper-x guard column copy the
adjacent interior column.

For a nonperiodic y axis in an ordinary freestream case, lower- and upper-y
normal v faces equal freestream v and their adjacent tangential u rows equal
freestream u. In a Poiseuille case, these are no-slip channel walls: both the
normal v faces and adjacent tangential u rows are zero.

For a periodic axis, the two stored duplicate endpoint faces are replaced by
their average. Solvers reapply these rules after advection/diffusion and after
projection, before publishing a completed state. Moving foil faces remain
governed by the shared geometry and wall-velocity contract.
