// A WebSocket the tests drive by hand: no network, no timers of its own.
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static reset() {
    FakeWebSocket.instances = [];
  }
  static get last() {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  close(code = 1000) {
    if (this.closed) return;
    this.closed = true;
    this.onclose?.({ code });
  }
  // Test helpers: what the server would do.
  open() {
    this.onopen?.();
  }
  receive(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
  drop(code = 1006) {
    this.close(code);
  }
}
