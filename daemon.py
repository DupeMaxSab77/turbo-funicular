import subprocess, time, os, signal

os.chdir('/root/turbo-funicular')
proc = subprocess.Popen(
    ['./venv/bin/python', 'server.py'],
    stdout=open('/tmp/srv_final.log', 'w'),
    stderr=subprocess.STDOUT,
    preexec_fn=os.setsid
)
print(f"Server PID: {proc.pid}", flush=True)

# Write PID to file so we can find it
with open('/tmp/server.pid', 'w') as f:
    f.write(str(proc.pid))

while True:
    time.sleep(60)
    if proc.poll() is not None:
        print(f"Server died (rc={proc.returncode}), restarting...", flush=True)
        proc = subprocess.Popen(
            ['./venv/bin/python', 'server.py'],
            stdout=open('/tmp/srv_final.log', 'a'),
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid
        )
        print(f"New PID: {proc.pid}", flush=True)
        with open('/tmp/server.pid', 'w') as f:
            f.write(str(proc.pid))
