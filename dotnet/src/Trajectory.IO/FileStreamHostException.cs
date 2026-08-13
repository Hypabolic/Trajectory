namespace Hypabolic.Trajectory.IO;

/// <summary>
/// Host-side file stream configuration or I/O failure.
/// Not a transcript diagnostic and must not be written into <c>StreamUpdate.Diagnostics</c>.
/// </summary>
public sealed class FileStreamHostException : Exception
{
    public const string RootRequired = "root_required";
    public const string PathRequired = "path_required";
    public const string PathOutsideRoot = "path_outside_root";
    public const string IoPermission = "io_permission";
    public const string IoNotFound = "io_not_found";
    public const string IoError = "io_error";

    public string Code { get; }

    /// <summary>For the calling process only; never copy into stream wire objects.</summary>
    public string? Path { get; }

    public FileStreamHostException(string code, string message, string? path = null, Exception? inner = null)
        : base(message, inner)
    {
        Code = code;
        Path = path;
    }
}
