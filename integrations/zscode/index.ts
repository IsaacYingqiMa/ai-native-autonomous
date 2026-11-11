import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { createAidevflow } from "../shared/omp-flow";
import { Bridge } from "./client";

export default function aidevflow(pi: ExtensionAPI): void {
  createAidevflow(pi, { runtime: "zscode", nativeRoot: ".zscode", displayName: "zscode" }, root => new Bridge(root, "zscode"));
}
