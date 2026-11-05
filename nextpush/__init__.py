"""NextPush: a zero-config web-managed file server for NextPull. It runs the unmodified SFTPGo program as a separate process (its WebDAV service)
and puts a small front end in front of it: a private admin web GUI, and a public listener with a discovery document and an access-key-gated WebDAV proxy."""

__version__ = "0.2.0"
SFTPGO_TESTED_VERSION = "2.7.6"
