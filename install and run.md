# Install & Run

```bash
docker run -d --name rapidleech --restart unless-stopped -p 80:8000 -p 6881:6881/tcp -p 6881:6881/udp -v rapidleech_downloads:/app/downloads -v rapidleech_data:/app/data -e SECRET_KEY=89a9d36022e1f26e66b1f25eedbc213c437de15254cf06e33a2aa0db253e7122 -e ARIA2_RPC_SECRET=40cb6bab12174468e5dc6dde5e2fa5dc arpudhanaresh/rapidleech:latest
```

## Update to latest image

```bash
docker stop rapidleech && docker rm rapidleech && docker rmi arpudhanaresh/rapidleech:latest && docker run -d --name rapidleech --restart unless-stopped -p 80:8000 -p 6881:6881/tcp -p 6881:6881/udp -v rapidleech_downloads:/app/downloads -v rapidleech_data:/app/data -e SECRET_KEY=89a9d36022e1f26e66b1f25eedbc213c437de15254cf06e33a2aa0db253e7122 -e ARIA2_RPC_SECRET=40cb6bab12174468e5dc6dde5e2fa5dc arpudhanaresh/rapidleech:latest
```
