# Native SMB and NFS recording mounts

Record Checks can manage read-only Linux mounts directly. Existing local/mounted
sources continue to work. Apply `alembic upgrade head` before using the new UI.

## Deployment

This feature is disabled by default. It requires Linux kernel CIFS/NFS support,
root in the service's own mount namespace, and permission to mount. Windows local
development keeps using existing paths; native mount actions fail with a clear
Linux prerequisite error. Docker Desktop is not a supported mount deployment.

Set these variables in `.env` for both the web and scheduler services:

```dotenv
BSNAP_RECORD_MOUNTS_ENABLED=true
BSNAP_RECORD_MOUNT_ALLOWED_NETWORKS=192.168.10.20/32,192.168.10.21/32
BSNAP_RECORD_NFS_ALLOW_SYS=false
```

Set a strong `ENCRYPTION_KEY` before saving SMB sources and keep it stable. The
existing AES-GCM utility encrypts the password; encryption failure rejects the
save instead of storing plaintext. Serve the admin interface over HTTPS so
credentials are encrypted between the browser and B-Snap too.

### Linux Docker with AppArmor

The image includes `cifs-utils` and `nfs-common`. Load the supplied restricted
AppArmor profile on the Linux host before starting the opt-in overlay:

```bash
sudo apparmor_parser -r deploy/apparmor/bsnap-record-mounts
docker compose -f docker-compose.yml -f docker-compose.record-mounts.yml up -d --build
```

The overlay replaces the base web service's `privileged: true` with
`privileged: false`, adds `SYS_ADMIN` only to web/scheduler, and restricts mount
operations to CIFS/NFS with `ro,nosuid,nodev,noexec` at generated record paths.
It keeps Docker's seccomp filtering. Do not use `privileged: true`, unconfined
AppArmor, host mount propagation, host filesystem mounts, or expose the Docker
socket to enable this feature. Hosts without AppArmor need an equivalent reviewed
SELinux policy; the supplied overlay deliberately requires its named profile.

`SYS_ADMIN` remains a significant privilege even with this profile. Use dedicated
containers, restrict access to trusted administrators, isolate storage traffic,
and keep the kernel and mount helpers patched. These controls do not make a
compromised privileged service equivalent to an unprivileged service.

Web and scheduler have separate mount namespaces and independent mountpoints.
Each automatically mounts a source before checking it; no shared mount volume is
needed. Mount status in the UI describes the web service only. Disabling/deleting
a source detaches its web mount immediately; scheduler cleanup occurs on the next
record-check cycle. Keep the scheduler running for that cleanup. Stopping its
container also disposes of its mount namespace. Failed unmounts (including busy
mounts) reject the action; B-Snap never uses forced or lazy unmounts.

For a non-container Linux installation, install `cifs-utils nfs-common`, apply an
equivalent service confinement policy, and run web/scheduler with appropriate
mount permissions. Do not grant a general passwordless `sudo mount` rule.

## Using Record Checks

Choose **Add Source → Connection → SMB or NFS**. Enter an allowlisted literal IP
and a single SMB share name (e.g. `recordings`) or absolute NFS export path
(e.g. `/exports/recordings`). Hostnames and free-form mount options are rejected.
B-Snap generates `/var/lib/bsnap/record-mounts/<source UUID>` automatically.

For SMB, supply a dedicated account with read-only server permissions and an
optional domain. Blank passwords on edit retain the existing encrypted password;
switching to NFS/local removes it. **Mount** checks the mount prerequisites;
**Run** mounts automatically and performs the folder check. **Unmount** also
disables automatic checks. Re-enable the source to resume scheduled checks.
Manual Mount/Run can inspect a disabled source without enabling scheduling.

SMB is fixed to dialect **3.1.1**, `seal` (encryption), and NTLMSSP authentication,
on TCP 445, with no SMB1/guest/plaintext fallback. Passwords are never returned by list APIs
or included in audit records, command arguments, process environment, or disk
credential files. A RAM-backed descriptor is inherited only by the mount helper
and closed immediately afterwards. Helper output is discarded to prevent leaks.
SMB DFS referrals are disabled (`nodfs`) so storage cannot redirect the connection
to a different server.

NFS uses **4.2 over TCP**. Its default `sec=krb5p` requires Kerberos infrastructure
on the client/server, including a correct keytab, SPNs, DNS/reverse DNS, clock
synchronization and `rpc.gssd`. Docker administrators must provision those
separately (the overlay does not install credentials or start the GSS daemon).
Although the UI requires a literal IP, Kerberos must resolve that IP to the
server's valid NFS service principal in the deployment. A failed Kerberos mount
does not fall back to AUTH_SYS.

Legacy NFS exports can use `sec=sys` only with
`BSNAP_RECORD_NFS_ALLOW_SYS=true`. This mode has no traffic encryption or
cryptographic user authentication: restrict exports to the client's IP, retain
root squashing, use server-side read-only exports, firewall TCP 2049, and place
the storage network behind a trusted network boundary/VPN. Do not expose it to
the Internet. Prefer SMB encryption or Kerberos whenever available.

## Safeguards and limits

- Only deployment-allowlisted literal IPs are accepted. Loopback, link-local,
  multicast, unspecified IPs, option injection, and path traversal are rejected.
  Also enforce storage IP/port restrictions with deployment egress firewall rules:
  the application allowlist governs requested mounts, while kernel NFS referrals
  and server-side namespace changes require network controls. Permit only the
  intended storage servers on TCP 445/2049 and necessary Kerberos infrastructure.
- Mount/state directories are private, service-owned and reject symlinks. UUID
  mountpoints prevent administrators from choosing privileged filesystem targets.
- The kernel mount table is checked for filesystem identity and safe flags;
  recorded ownership is required before reusing/replacing/unmounting a mount.
- Web worker mount/check/edit actions use bounded per-source file locks. Scheduler
  remounts changed configurations on the next check and removes disabled/deleted
  mounts in its own namespace.
- Admin authentication, cross-origin rejection and non-simple JSON/custom-header
  requests protect mutations. Mount/unmount and source actions are audited.
- Mount/unmount helpers have a 30-second timeout. NFS metadata reads use a soft
  mount with bounded retries, suitable for these read-only checks. Kernel/network
  I/O can still outlast a userspace timeout; monitor service health during storage
  outages and do not use these mounts for application writes.
- Folder `mtime` remains an activity indicator; this feature does not validate
  video contents or recursively scan recordings.

Options follow the [CIFS helper documentation](https://man7.org/linux/man-pages/man8/mount.cifs.8.html)
and [NFS security documentation](https://man7.org/linux/man-pages/man5/nfs.5.html).
