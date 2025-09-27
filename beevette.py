#!/usr/bin/env python3
"""
VetteBee-Comms: Bumblebee-style radio voice for Cole.
Mic -> band-limit (300–3400 Hz) -> ring mod -> light bit-crush -> output
Push-to-talk: hold Left Shift (configurable). 1..5 trigger stingers in ./samples
"""
import os, sys, math, time, queue, threading, argparse
import numpy as np
import sounddevice as sd

# ---- CLI ----
def parse_args():
    p = argparse.ArgumentParser(prog="VetteBee-Comms", description="Bumblebee-style radio voice")
    p.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    p.add_argument("--config", type=str, default="config.toml", help="Path to TOML config")
    p.add_argument("--in", dest="dev_in", type=int, default=None, help="Input device index")
    p.add_argument("--out", dest="dev_out", type=int, default=None, help="Output device index")
    p.add_argument("--always-on", action="store_true", help="Disable push-to-talk gate")
    p.add_argument("--beep", action="store_true", help="Play a startup beep")
    return p.parse_args()

args = parse_args()

# ---- Config (overridden by config.toml if present) ----
CFG = {
    "sample_rate": 48000,
    "block_size": 1024,
    "mic_channels": 1,
    "out_channels": 1,
    "band_low_hz": 300.0,
    "band_high_hz": 3400.0,
    "ringmod_hz": 90.0,
    "bit_depth": 10,
    "ptt_key": "shift",           # shift|ctrl|alt|space
    "idle_mute_db": -60,
    "output_gain_db": -3.0,
    "duck_samples_gain_db": -6.0,
}
# Load external config if present / from --config
try:
    import tomli
    if os.path.exists(args.config):
        with open(args.config,"rb") as f:
            CFG.update(tomli.load(f))
except Exception:
    pass

SR = int(CFG["sample_rate"])
BS = int(CFG["block_size"])

# ---- Push-to-talk via pynput (fails open to always-on) ----
PTT_HELD = False
try:
    from pynput import keyboard
    KM = {"shift": keyboard.Key.shift, "ctrl": keyboard.Key.ctrl,
          "alt": keyboard.Key.alt, "space": keyboard.Key.space}
    PTT_TARGET = KM.get(str(CFG["ptt_key"]).lower(), keyboard.Key.shift)

    def on_press(key):
        global PTT_HELD
        if key == PTT_TARGET: PTT_HELD = True
    def on_release(key):
        global PTT_HELD
        if key == PTT_TARGET: PTT_HELD = False
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.daemon = True
    listener.start()
except Exception as e:
    print("[WARN] PTT keyboard listener disabled:", e)
    PTT_HELD = True  # fallback: always transmit

# honor --always-on for quick tests
if args.always_on:
    PTT_HELD = True

def db_to_lin(db): return 10.0 ** (db/20.0)

# ---- Simple HP/LP filters (one-pole) ----
class OnePoleHP:
    def __init__(self, fc, fs):
        self.a = math.exp(-2*math.pi*fc/fs)
        self.z = 0.0
    def process(self, x):
        # crude HP: y = x - lp(x)
        y = np.empty_like(x)
        for i, s in enumerate(x):
            self.z = (1 - self.a)*s + self.a*self.z
            y[i] = s - self.z
        return y

class OnePoleLP:
    def __init__(self, fc, fs):
        self.a = math.exp(-2*math.pi*fc/fs)
        self.z = 0.0
    def process(self, x):
        y = np.empty_like(x)
        for i, s in enumerate(x):
            self.z = (1 - self.a)*s + self.a*self.z
            y[i] = self.z
        return y

hp = OnePoleHP(CFG["band_low_hz"], SR)
lp = OnePoleLP(CFG["band_high_hz"], SR)

# ---- Ring mod + bit crush state ----
phase = 0.0
phase_inc = 2*math.pi*CFG["ringmod_hz"]/SR
levels = float(2**CFG["bit_depth"] - 1)

# ---- Simple sample player (stingers) ----
import soundfile as sf
SLOTS = {}
def load_samples():
    d = "samples"
    if not os.path.isdir(d): return
    files = sorted([f for f in os.listdir(d) if f.lower().endswith(
        (".wav",".flac",".ogg",".mp3",".aiff",".aif",".m4a"))])
    for i, fn in enumerate(files[:5], 1):
        try:
            data, sr = sf.read(os.path.join(d, fn), dtype="float32", always_2d=False)
            if data.ndim > 1: data = data.mean(axis=1)
            if sr != SR:
                # linear resample
                ratio = SR/float(sr)
                idx = np.arange(0, len(data)*ratio, ratio)
                idx = np.clip(idx, 0, len(data)-1)
                data = np.interp(idx, np.arange(len(data)), data.astype(np.float32))
            SLOTS[str(i)] = data
            print(f"[SAMPLE] {i} -> {fn} ({len(data)/SR:.1f}s)")
        except Exception as e:
            print(f"[SAMPLE] Failed {fn}: {e}")

load_samples()
SAMPLE_Q = queue.Queue()
PLAYBUF = np.zeros(0, dtype=np.float32)
play_lock = threading.Lock()

def sample_hotkeys():
    try:
        from pynput import keyboard
        def on_press(key):
            try: k = key.char
            except: return
            if k in SLOTS: SAMPLE_Q.put(k)
        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
    except Exception as e:
        print("[WARN] Sample hotkeys disabled:", e)
sample_hotkeys()

def mix_samples(nframes):
    global PLAYBUF
    out = np.zeros(nframes, dtype=np.float32)
    while True:
        try: k = SAMPLE_Q.get_nowait()
        except queue.Empty: break
        with play_lock:
            PLAYBUF = np.concatenate([PLAYBUF, SLOTS[k]])
    with play_lock:
        if len(PLAYBUF) > 0:
            take = min(nframes, len(PLAYBUF))
            out[:take] += PLAYBUF[:take]
            PLAYBUF = PLAYBUF[take:]
    return np.tanh(out*1.5)

# ---- Devices ----
def list_devices_and_exit():
    for i,d in enumerate(sd.query_devices()):
        print(f"[{i:2d}] {d['name']:<40} in:{d['max_input_channels']} out:{d['max_output_channels']}")
    sys.exit(0)

def select_devices():
    if args.dev_in is not None or args.dev_out is not None:
        sd.default.device = (args.dev_in, args.dev_out)
    try:
        cur = sd.default.device
    except Exception:
        cur = None
    print(f"[AUDIO] devices -> in/out: {cur}")
    return cur

# ---- Audio callback ----
def audio_cb(indata, outdata, frames, time_info, status):
    global phase
    x = indata[:,0].copy() if indata.shape[1] else np.zeros(frames, dtype=np.float32)

    # band-limit
    x = hp.process(x)
    x = lp.process(x)

    # ring mod
    t = np.arange(frames, dtype=np.float32)
    carrier = np.sin(phase + phase_inc*t)
    phase = (phase + phase_inc*frames) % (2*np.pi)
    x = x * carrier

    # bit-crush
    x = np.clip(x, -1.0, 1.0)
    x = np.round((x + 1.0) * 0.5 * levels) / levels * 2.0 - 1.0

    # stingers + duck
    st = mix_samples(frames)
    if np.max(np.abs(st)) > 1e-4:
        x *= db_to_lin(CFG["duck_samples_gain_db"])
    y = x + st

    # ptt gate
    if not PTT_HELD:
        y *= db_to_lin(CFG["idle_mute_db"])

    # out gain + soft clip
    y *= db_to_lin(CFG["output_gain_db"])
    y = np.tanh(y * 1.5)

    outdata[:,0] = y
    if outdata.shape[1] > 1: outdata[:,1] = y

def main():
    # quick one-off modes
    if args.list_devices:
        return list_devices_and_exit()

    print("=== VetteBee-Comms 🐝 ===  Hold LEFT SHIFT to talk.  1..5: stingers")
    select_devices()

    if args.beep:
        try:
            t = np.linspace(0,0.2,int(SR*0.2),False)
            sd.play((np.sin(2*np.pi*880*t)*0.25).astype('float32'), SR); sd.wait()
        except Exception as _e:
            pass

    try:
        with sd.Stream(channels=(CFG["mic_channels"], CFG["out_channels"]),
                       samplerate=SR, blocksize=BS, dtype='float32',
                       callback=audio_cb):
            while True: time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[EXIT] Bye.")
    except Exception as e:
        print("[ERROR]", e); sys.exit(1)

if __name__ == "__main__":
    main()
