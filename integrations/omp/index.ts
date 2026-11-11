import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { createAidevflow } from "../shared/omp-flow";
import { Bridge } from "./client";

export default function aidevflow(pi: ExtensionAPI): void {
  createAidevflow(pi, { runtime: "omp", nativeRoot: ".omp", displayName: "OMP" }, root => new Bridge(root, "omp"));
}
