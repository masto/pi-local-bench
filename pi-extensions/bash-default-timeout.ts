// Give pi's bash tool a default timeout. pi has none, so a command that never
// exits (a dev server started in the foreground, a watcher, a test script that
// starts a game loop) blocks the whole session until someone kills it. Calls that
// set their own timeout are left alone. Override with PI_BASH_DEFAULT_TIMEOUT (seconds).
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const DEFAULT_TIMEOUT_SECONDS = Number(process.env.PI_BASH_DEFAULT_TIMEOUT) || 180;

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event) => {
    const input = event.input as { timeout?: number } | undefined;
    if (event.toolName === "bash" && input && input.timeout === undefined) {
      input.timeout = DEFAULT_TIMEOUT_SECONDS;
    }
  });
}
