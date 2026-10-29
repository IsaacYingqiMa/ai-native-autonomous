import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";

/** One worker per extension binding. Failed requests are never blindly replayed. */
export class Bridge {
  private child?: ChildProcessWithoutNullStreams;
  private nextId = 0;
  private pending = new Map<string, { resolve: (value: any) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout> }>();

  constructor(private readonly root: string) {}

  private start(): void {
    const config = JSON.parse(readFileSync(join(this.root, ".aidevflow/project.json"), "utf8"));
    if (typeof config.python !== "string" || !config.python) throw new Error("AIDevFlow Python is not configured; reinstall the runtime");
    const child = spawn(config.python, ["-B", join(this.root, "scripts/aidevflow_bridge.py"), this.root], {
      cwd: this.root, windowsHide: true, stdio: "pipe",
    });
    this.child = child;
    const lines = createInterface({ input: child.stdout });
    lines.on("line", line => {
      try {
        if (Buffer.byteLength(line) > 8 * 1024 * 1024) throw new Error("Oversized AIDevFlow reply");
        const reply = JSON.parse(line);
        const request = this.pending.get(reply.id);
        if (!request) return;
        clearTimeout(request.timer);
        this.pending.delete(reply.id);
        if (reply.ok) request.resolve(reply.result);
        else request.reject(new Error(reply.error || "AIDevFlow request rejected"));
      } catch (error) {
        this.fail(error instanceof Error ? error : new Error(String(error)));
      }
    });
    // Drain stderr without exposing command payloads or credentials to the model.
    child.stderr.resume();
    child.on("error", error => {
      if (this.child === child) { this.child = undefined; this.fail(error); }
    });
    child.on("exit", () => {
      if (this.child === child) {
        this.child = undefined;
        this.fail(new Error("AIDevFlow worker exited; inspect status before retrying"));
      }
      lines.close();
    });
  }

  request(action: string, payload: unknown = {}): Promise<any> {
    if (!this.child) this.start();
    const id = String(++this.nextId);
    const frame = JSON.stringify({ protocol: 1, id, action, payload }) + "\n";
    if (Buffer.byteLength(frame) > 8 * 1024 * 1024) return Promise.reject(new Error("AIDevFlow frame too large"));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("AIDevFlow request timed out; execution status is unknown, inspect before retrying"));
        this.close();
      }, 15000);
      this.pending.set(id, { resolve, reject, timer });
      this.child!.stdin.write(frame, "utf8", error => { if (error) this.fail(error); });
    });
  }

  private fail(error: Error): void {
    for (const request of this.pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    this.pending.clear();
  }

  close(): void {
    const child = this.child;
    this.child = undefined;
    this.fail(new Error("AIDevFlow worker closed"));
    child?.stdin.end();
    child?.kill();
  }
}
