import type {FlowSolver, SolverId} from "../core/contracts.js";
import {StableFluidsSolver} from "./stableFluids.js";
import {LbmSolver} from "./lbm.js";
import {PicFlipSolver} from "./picFlip.js";

export function createSolver(id: SolverId): FlowSolver {
  switch (id) {
    case "stable-fluids": return new StableFluidsSolver();
    case "lbm-d2q9": return new LbmSolver();
    case "pic-flip": return new PicFlipSolver();
    default: return unreachable(id);
  }
}

function unreachable(value: never): never {
  throw new Error(`unsupported solver id: ${String(value)}`);
}
