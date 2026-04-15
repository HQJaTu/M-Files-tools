# M-Files Downloads
https://product.m-files.com/downloads/

# Apache Tika

## Podman
Run in background:
```text
podman run -p 9998:9998 --detach apache/tika:latest
```

## On Memory
Large files will cause Tika to crash with a 'java.lang.OutOfMemoryError: Java heap space'

An example of a large PDF file is 400 MiB file size with 296 pages in it.

```text
podman run \
  -p 9998:9998 \
  --detach \
  -e TIKA_CHILD_OPTS="-Xmx4g" \
  --memory="5g" \
  apache/tika:latest
```

### Heap size:
Running something like `java -XX:+PrintFlagsFinal -version | grep MaxHeapSize` will state:
```text
   size_t MaxHeapSize                              = 501219328
   size_t SoftMaxHeapSize                          = 501219328
```

# Tunneling to Tika with Ngrok

## As front command
Command:
```text
ngrok http 9998
```

Will say something like:
```text
ngrok                                (Ctl+C to quit)

ngrok does not support a dynamic, color terminal UI on solaris.
Access the web interface for connection and tunnel status.

Version                       3.37.6
Region                        Auto (lowest latency) (auto)
Web Interface                 http://127.0.0.1:4040
```

## As service
Install service:
```text
ngrok service install --config /etc/ngrok/ngrok.yaml
```

Configure:
```yaml
version: "2"
authtoken: YOUR_NGROK_AUTHTOKEN
tunnels:
  web_app:
    proto: http
    addr: 9998
    #domain: your-static-domain.ngrok-free.app # Optional
```

Run service:
```text
service start ngrok
```

Find out the public address if not using paid version which can bind to a known domain:
```text
curl -s http://127.0.0.1:4040/api/tunnels | jq -r '.tunnels[].public_url'
```

Will return something like: https://6ab5-2a01-4f8-c0c-9cf9-00-1.ngrok-free.app

# Security considerations

## Windows

As the content of an eBook may be considered as "malicious" by real-time virus scanner,
adding the venv `python.exe` as a process exclusion may be needed.
