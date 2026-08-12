namespace Hypabolic.Trajectory.Ahp;

/// <summary>Bidirectional text-frame transport for AHP JSON-RPC.</summary>
public interface IAhpTransport
{
    void Send(string message);
    void SetHandler(Action<string>? handler);
    void Close();
}

/// <summary>One side of an in-memory duplex.</summary>
public sealed class MemoryAhpTransport : IAhpTransport
{
    private MemoryAhpTransport? _peer;
    private Action<string>? _handler;
    private bool _closed;
    private readonly List<string> _sent = new();

    public IReadOnlyList<string> Sent => _sent;
    public bool IsClosed => _closed;

    public void BindPeer(MemoryAhpTransport peer) => _peer = peer;

    public void Send(string message)
    {
        if (_closed) throw new InvalidOperationException("transport_closed");
        _sent.Add(message);
        var peer = _peer;
        if (peer is null || peer._closed) return;
        peer._handler?.Invoke(message);
    }

    public void SetHandler(Action<string>? handler) => _handler = handler;

    public void Close()
    {
        _closed = true;
        _handler = null;
    }
}

/// <summary>Linked client/host transports for fake-host CI tests.</summary>
public sealed class InMemoryAhpTransportPair
{
    public MemoryAhpTransport Client { get; } = new();
    public MemoryAhpTransport Host { get; } = new();

    public InMemoryAhpTransportPair()
    {
        Client.BindPeer(Host);
        Host.BindPeer(Client);
    }
}
