/** Injectable AHP message duplex (JSON-RPC text frames). */

export type MessageHandler = (message: string) => void;

export interface AhpTransport {
  send(message: string): void;
  setHandler(handler: MessageHandler | null): void;
  close(): void;
}

export class MemoryAhpTransport implements AhpTransport {
  private peer: MemoryAhpTransport | null = null;
  private handler: MessageHandler | null = null;
  private _closed = false;
  readonly sent: string[] = [];

  bindPeer(peer: MemoryAhpTransport): void {
    this.peer = peer;
  }

  send(message: string): void {
    if (this._closed) throw new Error("transport_closed");
    this.sent.push(message);
    const peer = this.peer;
    if (!peer || peer._closed) return;
    peer.handler?.(message);
  }

  setHandler(handler: MessageHandler | null): void {
    this.handler = handler;
  }

  close(): void {
    this._closed = true;
    this.handler = null;
  }

  get closed(): boolean {
    return this._closed;
  }
}

export class InMemoryAhpTransportPair {
  readonly client = new MemoryAhpTransport();
  readonly host = new MemoryAhpTransport();

  constructor() {
    this.client.bindPeer(this.host);
    this.host.bindPeer(this.client);
  }
}
