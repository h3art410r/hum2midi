"""Classical YIN pitch tracking for a monophonic hum (no ML or song template)."""
from __future__ import annotations
import argparse, json, subprocess, wave
from pathlib import Path
import imageio_ffmpeg
import mido
import numpy as np

SR, FRAME, HOP, FFT = 16000, 2048, 160, 4096

def convert(src: Path, dst: Path) -> None:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(dst)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def pitch_track(path: Path):
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SR):
            raise ValueError("Expected 16kHz mono PCM16 WAV")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(float) / 32768
    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP].copy()
    frames -= frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    z = np.fft.rfft(frames, n=FFT, axis=1)
    ac = np.fft.irfft(z * z.conj(), n=FFT, axis=1)[:, :FRAME]
    cs = np.pad(np.cumsum(frames ** 2, axis=1), ((0, 0), (1, 0)))
    lag = np.arange(1, 241)
    d = cs[:, FRAME-lag] + cs[:, -1, None] - cs[:, lag] - 2*ac[:, lag]
    d = np.maximum(d, 1e-12)
    cmnd = d * lag[None, :] / np.maximum(np.cumsum(d, axis=1), 1e-12)
    f0 = np.zeros(len(frames)); conf = np.zeros(len(frames))
    for i, row in enumerate(cmnd):
        band = row[19:229]
        cross = np.flatnonzero(band < .18)
        k = 19 + int(cross[0]) if cross.size else 19 + int(np.argmin(band))
        if not cross.size and row[k] > .32:
            continue
        while k+1 < 229 and row[k+1] < row[k]:
            k += 1
        tau = k+1
        if 0 < k < len(row)-1:
            a,b,c = row[k-1:k+2]; den = a-2*b+c
            if abs(den) > 1e-12: tau += .5*(a-c)/den
        f0[i] = SR/tau; conf[i] = 1-row[k]
    voiced = (rms > max(.008, np.quantile(rms,.2)*.45)) & (f0 > 0) & (conf >= .68)
    midi = np.zeros(len(f0)); midi[voiced] = 69+12*np.log2(f0[voiced]/440)
    pad = np.pad(midi, (5,5), mode="edge")
    med = np.median(np.lib.stride_tricks.sliding_window_view(pad,11),axis=1)
    midi[voiced] = med[voiced]
    pitches = np.rint(np.clip(midi,36,84)).astype(int)
    return pitches, voiced, len(x)/SR

def extract(pitches, voiced, duration):
    labels = np.where(voiced,pitches,-1); seg=[]; start=0
    for i in range(1,len(labels)+1):
        if i==len(labels) or labels[i]!=labels[start]:
            if labels[start]>=0 and i-start>=10: seg.append([start,i,int(labels[start])])
            start=i
    merged=[]
    for s in seg:
        if merged and s[2]==merged[-1][2] and s[0]-merged[-1][1]<=8: merged[-1][1]=s[1]
        else: merged.append(s[:])
    notes=[]; correction=FRAME/(2*SR)
    for a,b,p in merged:
        st=max(0,a*.01-correction); end=min(duration,b*.01-correction)
        if end-st>=.08: notes.append({"pitch":p,"start":round(st,3),"duration":round(end-st,3)})
    return notes

def write_midi(notes,path):
    mid=mido.MidiFile(ticks_per_beat=480); tr=mido.MidiTrack(); mid.tracks.append(tr)
    tr.append(mido.MetaMessage("set_tempo",tempo=mido.bpm2tempo(120))); events=[]
    for n in notes:
        events += [(round(n["start"]*960),1,n["pitch"]),(round((n["start"]+n["duration"])*960),0,n["pitch"])]
    tick=0
    for t,on,p in sorted(events,key=lambda e:(e[0],e[1])):
        tr.append(mido.Message("note_on" if on else "note_off",note=p,velocity=82 if on else 0,time=max(0,t-tick))); tick=t
    mid.save(path)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("audio",type=Path); ap.add_argument("--out",type=Path,default=Path("data/demo/dsp-midi")); a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True); wav=a.out/"input_16k.wav"; convert(a.audio,wav)
    p,v,d=pitch_track(wav); notes=extract(p,v,d)
    if not notes: raise SystemExit("No stable notes detected")
    (a.out/"notes.json").write_text(json.dumps({"method":"YIN_pitch_tracking","duration":d,"notes":notes},indent=2),encoding="utf-8")
    write_midi(notes,a.out/"melody.mid"); print(json.dumps(notes,indent=2)); print("MIDI:",a.out/"melody.mid")

if __name__=="__main__": main()
