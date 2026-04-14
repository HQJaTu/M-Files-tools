# M-Files Downloads
https://product.m-files.com/downloads/

# Apache Tika

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