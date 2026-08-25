import cv2
import numpy as np
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import json

APP = "DCardLabs Card Scanner v9.4.2"

# Reference geometry from the user's Brother MFC-L2710DN 600 dpi A4 scan.
BASE_W, BASE_H = 1448, 2048
BASE_CENTERS = [
    (233,315),(690,315),(1173,315),
    (242,956),(703,956),(1193,959),
    (237,1621),(719,1623),(1197,1613)
]

# Standard sports-card ratio, used only to estimate the expected card rectangle.
# The sleeve is intentionally NOT used as the crop boundary.
CARD_WIDTH = 432.0
CARD_HEIGHT = 605.0

JPEG_QUALITY = 97
ROTATE_180 = True
EDGE_SEARCH = 18

def line_from_fit(points):
    pts=np.asarray(points,np.float32)
    if len(pts)<30:
        return None
    vx,vy,x0,y0=cv2.fitLine(
        pts.reshape(-1,1,2),
        cv2.DIST_L1,0,0.01,0.01
    ).reshape(-1)
    a=vy
    b=-vx
    c=vx*y0-vy*x0
    n=(a*a+b*b)**0.5
    return (a/n,b/n,c/n) if n else None

def intersect(l1,l2):
    a1,b1,c1=l1
    a2,b2,c2=l2
    d=a1*b2-a2*b1
    if abs(d)<1e-8:
        return None
    return np.array([
        (b1*c2-b2*c1)/d,
        (c1*a2-c2*a1)/d
    ],np.float32)

def find_edge(gray,cx,cy,side,w=432.0,h=605.0,search=35):
    """
    Sleeve-aware Hough edge detection.

    We know approximately where the INNER card edge should be. Search a band
    around that position for a long straight edge, allowing the card itself
    to be tilted. The candidate closest to the expected card edge wins.
    """
    H,W=gray.shape
    if side in ("left","right"):
        expected=cx+(-w/2 if side=="left" else w/2)
        xa=max(0,int(expected-search))
        xb=min(W,int(expected+search))
        ya=max(0,int(cy-h/2-20))
        yb=min(H,int(cy+h/2+20))
        roi=gray[ya:yb,xa:xb]
        orient="vertical"
    else:
        expected=cy+(-h/2 if side=="top" else h/2)
        ya=max(0,int(expected-search))
        yb=min(H,int(expected+search))
        xa=max(0,int(cx-w/2-20))
        xb=min(W,int(cx+w/2+20))
        roi=gray[ya:yb,xa:xb]
        orient="horizontal"

    if roi.size==0:
        return None,[]

    edges=cv2.Canny(
        cv2.GaussianBlur(roi,(3,3),0),
        25,90
    )

    lines=cv2.HoughLinesP(
        edges,
        1,np.pi/720,
        threshold=35,
        minLineLength=int((h if orient=="vertical" else w)*0.45),
        maxLineGap=25
    )
    if lines is None:
        return None,[]

    candidates=[]
    for L in np.asarray(lines).reshape(-1,4):
        x1,y1,x2,y2=map(int,L)
        dx=x2-x1; dy=y2-y1
        length=(dx*dx+dy*dy)**0.5
        angle=np.degrees(np.arctan2(dy,dx))
        while angle>90: angle-=180
        while angle<=-90: angle+=180

        if orient=="vertical":
            if abs(abs(angle)-90)>8:
                continue
            midx=(x1+x2)/2+xa
            midy=(y1+y2)/2+ya
            distance=abs(midx-expected)
        else:
            if abs(angle)>8:
                continue
            midx=(x1+x2)/2+xa
            midy=(y1+y2)/2+ya
            distance=abs(midy-expected)

        if distance>search:
            continue

        # Prefer the edge closest to the expected INNER card boundary.
        # Long lines are preferred, but distance dominates so the sleeve's
        # outer edge (usually farther away) is not selected.
        score=length - 12.0*distance

        candidates.append(
            (score,length,distance,
             (x1+xa,y1+ya,x2+xa,y2+ya),angle)
        )

    if not candidates:
        return None,[]

    best=max(candidates,key=lambda z:z[0])
    x1,y1,x2,y2=best[3]
    # Convert selected line segment to normalized ax+by+c=0.
    a=y1-y2
    b=x2-x1
    c=x1*y2-x2*y1
    n=(a*a+b*b)**0.5
    line=(a/n,b/n,c/n) if n else None
    return line,[[x1,y1],[x2,y2]]

def fallback_line(cx,cy,side,w=432.0,h=605.0):
    if side=="left":
        return (-1,0,cx-w/2)
    if side=="right":
        return (-1,0,cx+w/2)
    if side=="top":
        return (0,-1,cy-h/2)
    return (0,-1,cy+h/2)

def order_quad(q):
    s=q.sum(axis=1)
    d=np.diff(q,axis=1).ravel()
    return np.array([
        q[np.argmin(s)],
        q[np.argmin(d)],
        q[np.argmax(s)],
        q[np.argmax(d)]
    ],np.float32)

def detect_card(gray,cx,cy):
    lines={}
    debug_points={}

    for side in ("left","right","top","bottom"):
        line,pts=find_edge(
            gray,cx,cy,side,
            CARD_WIDTH,CARD_HEIGHT,
            EDGE_SEARCH
        )
        if line is None:
            line=fallback_line(
                cx,cy,side,
                CARD_WIDTH,CARD_HEIGHT
            )
        lines[side]=line
        debug_points[side]=pts

    q=[
        intersect(lines["left"],lines["top"]),
        intersect(lines["right"],lines["top"]),
        intersect(lines["right"],lines["bottom"]),
        intersect(lines["left"],lines["bottom"])
    ]

    if any(p is None for p in q):
        q=np.array([
            [cx-CARD_WIDTH/2,cy-CARD_HEIGHT/2],
            [cx+CARD_WIDTH/2,cy-CARD_HEIGHT/2],
            [cx+CARD_WIDTH/2,cy+CARD_HEIGHT/2],
            [cx-CARD_WIDTH/2,cy+CARD_HEIGHT/2]
        ],np.float32)
    else:
        q=np.asarray(q,np.float32)

    return order_quad(q),debug_points


def trim_final_fringe(card, base_px=6, max_extra=10):
    """
    v9.4: keep v9.4's safe 6 px trim, then remove only a small residual
    neutral/bright fringe on individual sides. The detection is deliberately
    limited to the outer 4 px so it can never create the tiny crops seen in v10.
    """
    h,w=card.shape[:2]
    base_px=int(base_px)
    max_extra=int(max_extra)
    if h <= 2*(base_px+max_extra) or w <= 2*(base_px+max_extra):
        return card

    cropped=card[base_px:h-base_px, base_px:w-base_px]

    # Bright + low-chroma mask. White/very light gray sleeve/scanner fringe
    # is neutral; saturated card artwork is not.
    b,g,r=cv2.split(cropped)
    mx=np.maximum(np.maximum(b,g),r).astype(np.int16)
    mn=np.minimum(np.minimum(b,g),r).astype(np.int16)
    chroma=mx-mn
    neutral=(mx >= 200) & (chroma <= 35)

    def side_score(side, k):
        if side=='top':
            strip=neutral[k:k+1,:]
        elif side=='bottom':
            strip=neutral[-1:,:] if k==0 else neutral[-k-1:-k,:]
        elif side=='left':
            strip=neutral[:,k:k+1]
        else:
            strip=neutral[:,-1:] if k==0 else neutral[:,-k-1:-k]
        return float(np.mean(strip)) if strip.size else 0.0

    # Require a reasonably broad neutral fringe, but allow lower ratios than
    # v9.2 because sleeves can be partly transparent and include shadows.
    extra={'top':0,'bottom':0,'left':0,'right':0}
    threshold=0.45

    # Only continue inward while consecutive outer strips remain neutral.
    for side in extra:
        for k in range(max_extra):
            if side_score(side,k) >= threshold:
                extra[side]+=1
            else:
                break

    y1=extra['top']
    y2=cropped.shape[0]-extra['bottom']
    x1=extra['left']
    x2=cropped.shape[1]-extra['right']
    if x2<=x1 or y2<=y1:
        return cv2.resize(cropped,(w,h),interpolation=cv2.INTER_CUBIC)

    cropped=cropped[y1:y2,x1:x2]
    return cv2.resize(cropped,(w,h),interpolation=cv2.INTER_CUBIC)

def trim_one_pixel_fringe(card):
    """
v9.4.2: ultra-conservative final cosmetic pass.

    This function runs AFTER the complete v9.4 crop/perspective correction.
    It cannot change card detection. At most one pixel is removed from any
    side, and only when the outermost strip is strongly bright/neutral while
    the immediately adjacent strip is materially less neutral.
    """
    h,w=card.shape[:2]
    if h < 10 or w < 10:
        return card

    b,g,r=cv2.split(card)
    mx=np.maximum(np.maximum(b,g),r).astype(np.int16)
    mn=np.minimum(np.minimum(b,g),r).astype(np.int16)
    chroma=mx-mn
    neutral=(mx >= 205) & (chroma <= 28)

    def score(side, idx):
        if side=='top':
            strip=neutral[idx:idx+1,:]
        elif side=='bottom':
            strip=neutral[-idx-1:-idx if idx else None,:]
        elif side=='left':
            strip=neutral[:,idx:idx+1]
        else:
            strip=neutral[:,-idx-1:-idx if idx else None]
        return float(np.mean(strip)) if strip.size else 0.0

    remove={'top':0,'bottom':0,'left':0,'right':0}
    # Require a broad, almost continuous bright-neutral outer strip and a
    # clearly less-neutral second strip. This targets the last sleeve edge
    # without touching ordinary card artwork.
    for side in remove:
        outer=score(side,0)
        inner=score(side,1)
        if outer >= 0.78 and inner <= 0.55:
            remove[side]=1

    if not any(remove.values()):
        return card

    y1=remove['top']
    y2=h-remove['bottom']
    x1=remove['left']
    x2=w-remove['right']
    cropped=card[y1:y2,x1:x2]
    return cv2.resize(cropped,(w,h),interpolation=cv2.INTER_CUBIC)



# v0.8 – dynamic 3x3 grid detection
# No fixed BASE_W / BASE_H / BASE_CENTERS are used for card locations.

def _find_grid_components(image):
    """
    Find the nine actual card regions from the scan itself.
    The method uses the colored card artwork rather than the white sleeve.
    It is therefore independent of scan resolution and does not need a
    1448x2048 reference template.
    """
    H, W = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]

    best = None
    min_dim = min(H, W)
    kernel_size = max(3, int(round(min_dim * 0.0035)))
    if kernel_size % 2 == 0:
        kernel_size += 1

    for sat_thr in (20, 25, 30, 35, 40):
        mask = (sat > sat_thr).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (kernel_size, kernel_size)
        )
        cleaned = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(
            cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        candidates = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            area = cv2.contourArea(c)
            if w < W * 0.10 or h < H * 0.10:
                continue
            if w < 100 or h < 150:
                continue
            if area < 0.25 * w * h:
                continue
            ratio = w / float(h)
            if not 0.35 <= ratio <= 2.4:
                continue
            candidates.append((x, y, w, h, area, c))

        # Prefer exactly nine similarly sized, portrait-ish components.
        if len(candidates) >= 9:
            med_area = float(np.median([b[2] * b[3] for b in candidates]))
            scored = []
            for b in candidates:
                x, y, w, h, area, c = b
                size_score = abs(np.log((w * h) / med_area))
                aspect_score = abs(np.log((w / float(h)) / 0.72))
                score = size_score + 0.25 * aspect_score
                scored.append((score, b))
            scored.sort(key=lambda z: z[0])
            chosen = [z[1] for z in scored[:9]]

            # Validate a real 3x3 layout.
            centers = np.array(
                [[b[0] + b[2] / 2, b[1] + b[3] / 2] for b in chosen],
                np.float32
            )
            ys = np.sort(centers[:, 1])
            row_gaps = np.diff(ys)
            if len(row_gaps) == 8:
                # The two largest Y gaps should separate the three rows.
                sep = np.sort(row_gaps)[-2:]
                if sep[0] > max(10.0, np.median(row_gaps) * 1.25):
                    best = chosen
                    break

        # Keep the best 9 candidates as a fallback.
        if len(candidates) == 9:
            best = candidates

    if best is None or len(best) != 9:
        raise RuntimeError(
            "Die dynamische 3x3-Erkennung konnte nicht genau 9 Karten finden. "
            "Bitte den kompletten Scan mit allen 9 Karten verwenden."
        )

    # Sort into 3 rows and 3 columns using the actual detected centers.
    best = sorted(best, key=lambda b: b[1] + b[3] / 2)
    ordered = []
    for i in range(0, 9, 3):
        row = sorted(
            best[i:i+3],
            key=lambda b: b[0] + b[2] / 2
        )
        ordered.extend(row)

    return ordered


def _auto_orient_scan(image):
    """
    Detect whether the cards themselves are landscape. If so, rotate the
    complete scan once. This makes portrait-card extraction independent of
    whether the scanner exported the sheet portrait or landscape.
    """
    comps = _find_grid_components(image)
    median_ratio = float(np.median([b[2] / float(b[3]) for b in comps]))

    if median_ratio > 1.10:
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
        comps = _find_grid_components(image)

    return image, comps


def _quad_from_component(image, component):
    """
    Start from the actual detected card region and refine it with the same
    local Hough edge logic used by v9.4.2. The detector is no longer tied to
    fixed pixel coordinates, while the sleeve-aware edge search is retained.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    x, y, w, h, area, contour = component
    rect = cv2.minAreaRect(contour)
    q = order_quad(cv2.boxPoints(rect))

    cx = float(np.mean(q[:, 0]))
    cy = float(np.mean(q[:, 1]))
    rw = float(max(50, np.max(q[:, 0]) - np.min(q[:, 0])))
    rh = float(max(80, np.max(q[:, 1]) - np.min(q[:, 1])))

    # Refine only close to the dynamically detected card rectangle.
    # The contour/minAreaRect is the fallback and prevents wild excursions.
    lines = {}
    for side in ("left", "right", "top", "bottom"):
        line, pts = find_edge(
            gray, cx, cy, side, rw, rh,
            search=max(12, int(round(min(rw, rh) * 0.035)))
        )
        lines[side] = line

    refined = [
        intersect(lines["left"], lines["top"]) if lines["left"] and lines["top"] else None,
        intersect(lines["right"], lines["top"]) if lines["right"] and lines["top"] else None,
        intersect(lines["right"], lines["bottom"]) if lines["right"] and lines["bottom"] else None,
        intersect(lines["left"], lines["bottom"]) if lines["left"] and lines["bottom"] else None,
    ]

    # If refinement is geometrically sensible, use it; otherwise keep the
    # actual component quadrilateral.
    if not any(p is None for p in refined):
        rq = order_quad(np.asarray(refined, np.float32))
        rw2 = np.linalg.norm(rq[1] - rq[0])
        rh2 = np.linalg.norm(rq[3] - rq[0])
        if (
            0.75 * rw <= rw2 <= 1.30 * rw
            and 0.75 * rh <= rh2 <= 1.30 * rh
        ):
            return rq

    return q


def process(scan_path, out_dir, quality=97, rotate=True):
    image = cv2.imread(str(scan_path))
    if image is None:
        raise RuntimeError("Scan konnte nicht geladen werden.")

    # Dynamic orientation + 3x3 detection. No reference resolution.
    image, components = _auto_orient_scan(image)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    debug = image.copy()
    files = []
    debug_data = []

    for i, component in enumerate(components, 1):
        q = _quad_from_component(image, component)

        cv2.polylines(
            debug, [np.int32(q)], True, (0, 0, 255), 3
        )
        for p in q:
            cv2.circle(
                debug, tuple(np.int32(p)), 6, (0, 255, 0), -1
            )

        center = (
            int(q[:, 0].mean()),
            int(q[:, 1].mean())
        )
        cv2.putText(
            debug, str(i),
            (center[0] - 15, center[1]),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2, (0, 0, 255), 3, cv2.LINE_AA
        )

        target_w, target_h = 630, 880
        dst = np.array([
            [0, 0],
            [target_w - 1, 0],
            [target_w - 1, target_h - 1],
            [0, target_h - 1]
        ], np.float32)

        M = cv2.getPerspectiveTransform(q, dst)
        card = cv2.warpPerspective(
            image, M, (target_w, target_h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE
        )

        # Proven v9.4.2 finishing pipeline.
        card = trim_final_fringe(card, base_px=6, max_extra=10)
        card = trim_one_pixel_fringe(card)

        if rotate:
            card = cv2.rotate(card, cv2.ROTATE_180)

        path = out / f"{i:03d}.jpg"
        cv2.imwrite(
            str(path), card,
            [
                cv2.IMWRITE_JPEG_QUALITY, int(quality),
                cv2.IMWRITE_JPEG_OPTIMIZE, 1
            ]
        )
        files.append(path)

        debug_data.append({
            "card": i,
            "component": list(map(float, component[:5])),
            "quad": q.tolist()
        })

    cv2.imwrite(str(out / "DEBUG_Erkennung.png"), debug)
    (out / "_detection_debug.json").write_text(
        json.dumps(debug_data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Contact sheet retained from v9.4.2.
    sheet = np.full((1350, 960, 3), 255, np.uint8)
    for i, f in enumerate(files):
        c = cv2.imread(str(f))
        h, w = c.shape[:2]
        scale = min(300 / w, 430 / h)
        c = cv2.resize(
            c,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA
        )
        h, w = c.shape[:2]
        x = (i % 3) * 320 + (320 - w) // 2
        y = (i // 3) * 450 + (450 - h) // 2
        sheet[y:y+h, x:x+w] = c

    cv2.imwrite(
        str(out / "Kontaktbogen.jpg"),
        sheet,
        [cv2.IMWRITE_JPEG_QUALITY, 95]
    )
    return files


def gui():
    root = tk.Tk()
    root.title("DCardLabs Card Scanner v0.8 – Dynamic Grid")
    root.geometry("760x470")
    root.resizable(False, False)

    scan_var = tk.StringVar()
    out_var = tk.StringVar()
    rotate_var = tk.BooleanVar(value=True)
    quality_var = tk.IntVar(value=97)
    status = tk.StringVar(value="Bereit.")

    def pick_scan():
        p = filedialog.askopenfilename(
            title="Scan auswählen",
            filetypes=[
                ("Bilder", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                ("Alle Dateien", "*.*")
            ]
        )
        if p:
            scan_var.set(p)

    def pick_out():
        p = filedialog.askdirectory(title="Ausgabeordner auswählen")
        if p:
            out_var.set(p)

    def run():
        if not scan_var.get():
            messagebox.showwarning(
                "DCardLabs Card Scanner v0.8",
                "Bitte zuerst einen Scan auswählen."
            )
            return
        if not out_var.get():
            out_var.set(str(
                Path(scan_var.get()).parent /
                (Path(scan_var.get()).stem + "_DCardLabs")
            ))
        try:
            status.set("Erkenne 3×3-Kartenraster …")
            root.update_idletasks()
            files = process(
                scan_var.get(),
                out_var.get(),
                quality_var.get(),
                rotate_var.get()
            )
            status.set(f"Fertig: {len(files)} Karten gespeichert.")
            messagebox.showinfo(
                "DCardLabs Card Scanner v0.8",
                f"{len(files)} Karten wurden erstellt.\n\n"
                f"Ausgabe:\n{out_var.get()}"
            )
        except Exception as e:
            status.set("Fehler.")
            messagebox.showerror(
                "DCardLabs Card Scanner v0.8",
                f"{type(e).__name__}: {e}"
            )

    frm = ttk.Frame(root, padding=18)
    frm.pack(fill="both", expand=True)
    ttk.Label(
        frm,
        text="DCardLabs Card Scanner v0.8 – Dynamic Grid",
        font=("Segoe UI", 18, "bold")
    ).pack(anchor="w")
    ttk.Label(
        frm,
        text=(
            "Erkennt die 9 Karten direkt aus dem jeweiligen Scan. "
            "Keine feste 1448×2048-Vorlage und keine festen Kartenkoordinaten."
        ),
        wraplength=700
    ).pack(anchor="w", pady=(0, 18))

    for label, var, cmd in [
        ("Scan-Datei:", scan_var, pick_scan),
        ("Ausgabe:", out_var, pick_out)
    ]:
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=5)
        ttk.Label(row, text=label, width=16).pack(side="left")
        ttk.Entry(row, textvariable=var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(row, text="Auswählen…", command=cmd).pack(
            side="left", padx=(8, 0)
        )

    opt = ttk.LabelFrame(frm, text="Einstellungen", padding=12)
    opt.pack(fill="x", pady=15)
    ttk.Checkbutton(
        opt, text="Karten um 180° drehen", variable=rotate_var
    ).grid(row=0, column=0, sticky="w", padx=8, pady=5)
    ttk.Label(opt, text="JPG-Qualität:").grid(
        row=1, column=0, sticky="w", padx=8, pady=5
    )
    ttk.Spinbox(
        opt, from_=90, to=100, textvariable=quality_var, width=6
    ).grid(row=1, column=1, sticky="w")

    ttk.Button(
        frm,
        text="▶  3×3 KARTEN ERKENNEN & VERARBEITEN",
        command=run
    ).pack(fill="x", pady=12, ipady=8)
    ttk.Label(frm, textvariable=status).pack(anchor="w")
    root.mainloop()

if __name__ == "__main__":
    gui()
