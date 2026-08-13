# Cross-language fidelity cases

Status: proposed Revision 5 normative component.

Revision 5 will replace implementation-local fidelity parameters with one
schema-validated fixture. Each case fixes precision, domain, resolution,
duration, output interval, initial/control history, measured region, solid
mask treatment, norm, and per-family tolerance. A language may choose stable
internal substeps but may not shorten or coarsen the declared physical run.

The required cases are:

1. uniform periodic flow: component drift, density drift where applicable,
   divergence, and spurious vorticity;
2. Taylor–Green: the declared analytic cell-centred field, velocity L2 error,
   and kinetic-energy decay;
3. planar Poiseuille: analytic profile L2 error and wall leakage, excluding
   explicitly named inlet/outlet guard cells;
4. NACA 0012 at zero geometric angle: symmetry and solid penetration;
5. dynamic NACA 2412: wake width, recirculation area, enstrophy, mixing,
   leakage, and recovery time without a composite truth or visual score.

Analytic formulas, mask dilation, boundary stencil, denominators, and time at
which each measurement is taken belong in the fixture or its owning contract.
Native tests may retain additional small cases, but acceptance evidence must
consume the shared parameters exactly.

