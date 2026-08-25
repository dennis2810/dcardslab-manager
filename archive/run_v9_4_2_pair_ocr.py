import sys, json, shutil
from pathlib import Path
import cv2
import scanner_v9_4_2 as engine

import re, cv2
try:
    import pytesseract
except Exception:
    pytesseract = None

def normalize_ocr_line(line):
    line = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ' -]", " ", line)
    return re.sub(r"\s+", " ", line).strip()

def looks_like_name(line):
    line=normalize_ocr_line(line)
    if not line: return False
    up=line.upper()
    words=up.split()
    if not (2 <= len(words) <= 5): return False
    if sum(c.isalpha() for c in line) < 8: return False
    blocked=("PARIS","SAINT","GERMAIN","BUNDESLIGA","MIDFIELDER","DEFENDER","FORWARD","GOALKEEPER","GOLD","ELITE")
    if any(x in up for x in blocked): return False
    if any(len(w)<=1 for w in words): return False
    return True

def ocr_card_name(card):
    if pytesseract is None:
        return "", "pytesseract nicht installiert"
    try:
        h,w=card.shape[:2]
        crop=card[int(h*0.74):int(h*0.96),:]
        crop=cv2.resize(crop,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
        gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        gray=cv2.normalize(gray,None,0,255,cv2.NORM_MINMAX)
        variants=[gray,cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]]
        candidates=[]
        for vi,v in enumerate(variants):
            for psm in (6,11):
                data=pytesseract.image_to_data(v,config=f'--psm {psm}',lang='eng',output_type=pytesseract.Output.DICT)
                groups={}
                for i,txt in enumerate(data.get('text',[])):
                    txt=txt.strip()
                    if not txt: continue
                    try: conf=float(data['conf'][i])
                    except: conf=0
                    key=(data.get('block_num',[0])[i],data.get('par_num',[0])[i],data.get('line_num',[0])[i])
                    groups.setdefault(key,[]).append((txt,conf))
                for vals in groups.values():
                    line=normalize_ocr_line(' '.join(x[0] for x in vals))
                    if not looks_like_name(line): continue
                    conf=sum(x[1] for x in vals)/len(vals)
                    up=line.upper()
                    score=conf+(12 if len(up.split()) in (2,3) else 0)+(8 if line==up else 0)+(vi==0)*3+(psm==11)*2
                    candidates.append((score,line))
                raw=pytesseract.image_to_string(v,config=f'--psm {psm}',lang='eng')
                for line in raw.splitlines():
                    line=normalize_ocr_line(line)
                    if looks_like_name(line):
                        up=line.upper()
                        score=sum(c.isalpha() for c in line)+(12 if len(up.split()) in (2,3) else 0)+(8 if line==up else 0)+(psm==11)*2
                        candidates.append((score,line))
        if not candidates: return "", "nicht erkannt"
        candidates.sort(key=lambda x:(x[0],len(x[1])),reverse=True)
        return candidates[0][1], "ok"
    except Exception as e:
        return "", f"OCR-Fehler: {e}"


ENGINE_HASH="c4720e8622687ad9d64fa2f2cfde6a9aa595b3a843e3d02d5aa10fe58aa7a203"

def prepare_input(path, temp_dir):
    p=Path(path)
    image=cv2.imread(str(p))
    if image is not None:
        return p
    try:
        from PIL import Image
    except ModuleNotFoundError:
        raise RuntimeError("Pillow fehlt. Bitte einmal 'py -m pip install Pillow' ausführen.")
    temp_dir.mkdir(parents=True,exist_ok=True)
    out=temp_dir/(p.stem+"_normalized.jpg")
    with Image.open(p) as im:
        im.load()
        im.convert("RGB").save(out,format="JPEG",quality=100,subsampling=0)
    if cv2.imread(str(out)) is None:
        raise RuntimeError(f"Scan konnte nicht geladen werden: {p}")
    return out

def run(front,back,output,quality=97,rotate=True,do_ocr=True):
    output=Path(output)
    front_dir=output/"Vorderseite"
    back_dir=output/"Rueckseite"
    pairs_dir=output/"Paare"
    temp_dir=output/"_input_normalized"
    front_in=prepare_input(front,temp_dir)
    back_in=prepare_input(back,temp_dir)

    front_files=engine.process(front_in,front_dir,quality,rotate)
    back_files=engine.process(back_in,back_dir,quality,rotate)

    front_records=[]
    back_records=[]
    for p in front_files:
        card=int(Path(p).stem)
        name,status="", ""
        if do_ocr:
            img=cv2.imread(str(p))
            name,status=ocr_card_name(img)
        front_records.append({"card":card,"file":str(Path(p).resolve()),"name":name,"ocr_status":status})
    for p in back_files:
        card=int(Path(p).stem)
        back_records.append({"card":card,"file":str(Path(p).resolve())})

    back_map={r["card"]:r for r in back_records}
    pairs=[]
    pairs_dir.mkdir(parents=True,exist_ok=True)
    for fr in front_records:
        br=back_map.get(fr["card"])
        if not br: continue
        d=pairs_dir/f"{fr['card']:03d}"
        d.mkdir(exist_ok=True)
        safe=re.sub(r"[^A-Za-z0-9ÄÖÜäöüß _-]","",fr["name"]).strip()
        base=f"{fr['card']:03d}" + (f"_{safe}" if safe else "")
        fp=d/f"{base}_Vorderseite.jpg"
        bp=d/f"{base}_Rueckseite.jpg"
        shutil.copy2(fr["file"],fp)
        shutil.copy2(br["file"],bp)
        pairs.append({"card":fr["card"],"name":fr["name"],"ocr_status":fr["ocr_status"],"front":str(fp.resolve()),"back":str(bp.resolve())})

    import csv
    with (pairs_dir/"karten_paarung.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f,delimiter=";")
        w.writerow(["Karte","Name","OCR Status","Vorderseite","Rückseite"])
        for r in pairs: w.writerow([r["card"],r["name"],r["ocr_status"],r["front"],r["back"]])
    (pairs_dir/"karten_paarung.json").write_text(json.dumps(pairs,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"front_count":len(front_files),"back_count":len(back_files),"pair_count":len(pairs),"pairs":pairs,"output":str(output.resolve())}


if __name__ == "__main__":
    if len(sys.argv) < 7:
        raise SystemExit("Aufruf: run_v9_4_2_pair_ocr.py FRONT BACK OUTPUT QUALITY ROTATE OCR")
    result = run(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        int(sys.argv[4]),
        bool(int(sys.argv[5])),
        bool(int(sys.argv[6]))
    )
    print(json.dumps(result, ensure_ascii=False))
