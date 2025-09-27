# CorvCommander
VetteBee-Comms: push-to-talk “Bumblebee radio” chain (band-limit + ring-mod + bit-crush) for Bluetooth→FM.

## Quick start

## Build
### Linux/macOS
```bash
make dev
make build
./dist/VetteBee-Comms --beep --always-on
```

### Windows (PowerShell)
```powershell
.uild.ps1
.dist\VetteBee-Comms.exe --beep --always-on
```

## CLI
```
--list-devices            # show inputs/outputs
--in N  --out M           # force device indices
--always-on               # bypass PTT gate
--beep                    # startup chirp
--config path             # alternate TOML
```

```bash
make dev
make run

