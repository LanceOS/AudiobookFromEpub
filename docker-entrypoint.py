#!/usr/bin/env python3
import os
import sys

def recursive_chown(path: str, uid: int, gid: int) -> None:
    try:
        if os.path.exists(path):
            for root, dirs, files in os.walk(path, topdown=False):
                for name in files:
                    try:
                        os.chown(os.path.join(root, name), uid, gid)
                    except Exception:
                        pass
                for name in dirs:
                    try:
                        os.chown(os.path.join(root, name), uid, gid)
                    except Exception:
                        pass
            try:
                os.chown(path, uid, gid)
            except Exception:
                pass
    except Exception:
        pass


def drop_privileges(uid: int = 10001, gid: int = 10001) -> None:
    try:
        # Set group then user
        os.setgid(gid)
        os.setuid(uid)
    except Exception:
        pass


def main() -> None:
    # Ensure mounted volumes are writable by the unprivileged app user
    for p in ("/app/generated_audio", "/app/.app_data"):
        recursive_chown(p, 10001, 10001)

    # Ensure app user's home and Hugging Face cache directories exist and
    # are writable by the unprivileged `appuser` (UID/GID 10001). Some HF
    # libraries write tokens to "$HOME/.cache/huggingface/token"; if HOME
    # points to /root the unprivileged user cannot write there.
    app_home = "/home/appuser"
    try:
        os.makedirs(app_home, exist_ok=True)
    except Exception:
        pass

    cache_dir = os.path.join(app_home, ".cache")
    hf_cache = os.path.join(cache_dir, "huggingface")
    try:
        os.makedirs(hf_cache, exist_ok=True)
    except Exception:
        pass

    # Expose HOME/XDG cache env so libraries use the app user's cache location
    os.environ["HOME"] = app_home
    os.environ["XDG_CACHE_HOME"] = cache_dir
    os.environ.setdefault("HF_HOME", hf_cache)

    # Make sure the cache/home dirs are owned by appuser before dropping
    recursive_chown(app_home, 10001, 10001)
    recursive_chown(cache_dir, 10001, 10001)
    recursive_chown(hf_cache, 10001, 10001)

    # Drop privileges and exec the CMD as `appuser`.
    drop_privileges(10001, 10001)

    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])
    else:
        # Fallback: run default app
        os.execvp("python", ["python", "main.py", "--host", "0.0.0.0", "--port", "5000"]) 


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
import os
import sys
import pwd


def chown_tree(path: str, uid: int, gid: int) -> None:
    try:
        for root, dirs, files in os.walk(path):
            try:
                os.chown(root, uid, gid)
            except Exception:
                pass
            for d in dirs:
                try:
                    os.chown(os.path.join(root, d), uid, gid)
                except Exception:
                    pass
            for f in files:
                try:
                    os.chown(os.path.join(root, f), uid, gid)
                except Exception:
                    pass
        try:
            os.chown(path, uid, gid)
        except Exception:
            pass
    except Exception:
        pass


def main() -> None:
    # Default app user UID/GID (created in Dockerfile)
    default_uid = 10001
    default_gid = 10001
    paths = ["/app/generated_audio", "/app/.app_data"]

    for p in paths:
        try:
            if os.path.exists(p):
                chown_tree(p, default_uid, default_gid)
            else:
                os.makedirs(p, exist_ok=True)
                try:
                    os.chown(p, default_uid, default_gid)
                except Exception:
                    pass
        except Exception:
            try:
                os.chmod(p, 0o777)
            except Exception:
                pass

    # Drop privileges to `appuser` if available
    try:
        pw = pwd.getpwnam("appuser")
        uid = pw.pw_uid
        gid = pw.pw_gid
        try:
            os.setgid(gid)
            os.setuid(uid)
            os.environ["HOME"] = pw.pw_dir
        except Exception:
            # If we cannot drop privileges, continue as-is (best-effort)
            pass
    except KeyError:
        # appuser not present; continue as root
        pass

    # Exec the requested command (CMD will be provided by Dockerfile/compose)
    if len(sys.argv) > 1:
        os.execvp(sys.argv[1], sys.argv[1:])
    else:
        os.execvp("python", ["python", "main.py", "--host", "0.0.0.0", "--port", "5000"])


if __name__ == "__main__":
    main()
