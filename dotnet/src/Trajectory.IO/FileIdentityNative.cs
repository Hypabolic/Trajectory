using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Win32.SafeHandles;

namespace Hypabolic.Trajectory.IO;

/// <summary>
/// Platform file identity (volume/device + file index/inode) for same-size
/// replacement detection. Windows uses GetFileInformationByHandle; Unix uses
/// the runtime <c>System.Native</c> stat shim (stable FileStatus layout).
/// </summary>
internal static partial class FileIdentityNative
{
    internal static bool TryGet(string path, out ulong volumeSerial, out ulong fileIndex)
    {
        volumeSerial = 0;
        fileIndex = 0;
        try
        {
            if (OperatingSystem.IsWindows())
            {
                return TryGetWindows(path, out volumeSerial, out fileIndex);
            }

            return TryGetUnix(path, out volumeSerial, out fileIndex);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or DllNotFoundException)
        {
            return false;
        }
    }

    [SupportedOSPlatform("windows")]
    private static bool TryGetWindows(string path, out ulong volumeSerial, out ulong fileIndex)
    {
        volumeSerial = 0;
        fileIndex = 0;
        using var handle = File.OpenHandle(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);
        if (!GetFileInformationByHandle(handle, out var info))
        {
            return false;
        }

        volumeSerial = info.VolumeSerialNumber;
        fileIndex = ((ulong)info.FileIndexHigh << 32) | info.FileIndexLow;
        return true;
    }

    [UnsupportedOSPlatform("windows")]
    private static bool TryGetUnix(string path, out ulong volumeSerial, out ulong fileIndex)
    {
        volumeSerial = 0;
        fileIndex = 0;
        if (Stat(path, out var status) != 0)
        {
            return false;
        }

        volumeSerial = unchecked((ulong)status.Dev);
        fileIndex = unchecked((ulong)status.Ino);
        return fileIndex != 0 || volumeSerial != 0;
    }

    [SupportedOSPlatform("windows")]
    [LibraryImport("kernel32", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetFileInformationByHandle(
        SafeFileHandle hFile,
        out ByHandleFileInformation lpFileInformation);

    [UnsupportedOSPlatform("windows")]
    [LibraryImport("System.Native", EntryPoint = "SystemNative_Stat", StringMarshalling = StringMarshalling.Utf8, SetLastError = true)]
    private static partial int Stat(string path, out FileStatus output);

    [StructLayout(LayoutKind.Sequential)]
    private struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileTime
    {
        public uint LowDateTime;
        public uint HighDateTime;
    }

    /// <summary>
    /// Matches the .NET runtime <c>FileStatus</c> prefix (net8+) with the later
    /// trailing <c>HardLinkCount</c> so newer runtimes cannot write past the buffer.
    /// </summary>
    [StructLayout(LayoutKind.Sequential)]
    private struct FileStatus
    {
        public int Flags;
        public int Mode;
        public uint Uid;
        public uint Gid;
        public long Size;
        public long ATime;
        public long ATimeNsec;
        public long MTime;
        public long MTimeNsec;
        public long CTime;
        public long CTimeNsec;
        public long BirthTime;
        public long BirthTimeNsec;
        public long Dev;
        public long RDev;
        public long Ino;
        public uint UserFlags;
        public uint HardLinkCount;
    }
}
