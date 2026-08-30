# Export File Contract

The export file is a plain line-based file intended for a customer-side consumer script.

One line is one ready-to-copy VPN client artifact:

```text
client-001 main amneziawg <ready-client-link>
client-001 main vless_xhttp vless://...
client-001 main vless_tcp vless://...
```

Fields:

```text
client_id server_role protocol ready_link
```

Rules:

- `client_id`, `server_role`, and `protocol` do not contain spaces.
- `server_role` is `main` or `reserve`.
- `protocol` is `amneziawg`, `vless_xhttp`, or `vless_tcp`.
- `ready_link` is the exact string to copy and paste into the VPN client.
- Monday upload is out of scope here.

