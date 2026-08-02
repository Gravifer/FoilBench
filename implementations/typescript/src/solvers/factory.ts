import type {FlowSolver, SolverId} from "../core/contracts.js";
import {StableFluidsSolver} from "./stableFluids.js";
import {LbmSolver} from "./lbm.js";
import {PicFlipSolver} from "./picFlip.js";

export function createSolver(id: SolverId): FlowSolver {
  if (id === "stable-fluids") return new StableFluidsSolver();
  if (id === "lbm-d2q9") return new LbmSolver();
  return new PicFlipSolver();
}
