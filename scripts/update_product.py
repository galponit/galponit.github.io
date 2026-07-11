# -*- coding: utf-8 -*-
"""
Actualiza la pagina de un producto en galponit.com con los datos del ultimo
release del repo fuente (descripcion de la ficha de Play, links, version).

La version y el changelog NO se muestran visualmente: solo se registran en
catalog/<slug>.json y en un comentario HTML (trazabilidad).

Uso:
    python scripts/update_product.py --slug truco-mundial                  # dry-run (muestra diff)
    python scripts/update_product.py --slug truco-mundial --apply          # escribe los cambios
    python scripts/update_product.py --slug truco-mundial --youtube-url URL --apply

El repo fuente se toma de catalog/<slug>.json (campo sourceRepo); se puede
sobreescribir con --source.
"""
import argparse
import difflib
import hashlib
import html as html_mod
import json
import os
import re
import shutil
import sys
import datetime

# Consola Windows (cp1252) no imprime emojis: forzar UTF-8 en stdout/stderr
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.abspath(os.path.join(HERE, ".."))


def die(msg):
    print(f"ERROR: {msg}")
    sys.exit(1)


def load_catalog(slug):
    path = os.path.join(SITE, "catalog", f"{slug}.json")
    if not os.path.exists(path):
        die(f"No existe catalog/{slug}.json — dar de alta el producto primero.")
    with open(path, encoding="utf-8") as f:
        return json.load(f), path


def read_source_data(source_repo, catalog):
    """Lee version y descripcion desde el repo del producto."""
    app_json = os.path.join(source_repo, "app.json")
    if not os.path.exists(app_json):
        die(f"No se encontro {app_json} — verificar sourceRepo.")
    with open(app_json, encoding="utf-8") as f:
        expo = json.load(f).get("expo", {})
    version = expo.get("version")
    version_code = expo.get("android", {}).get("versionCode")

    desc_path = os.path.join(source_repo, "fastlane", "metadata", "android",
                             "es-419", "short_description.txt")
    if not os.path.exists(desc_path):
        die(f"No se encontro {desc_path}.")
    with open(desc_path, encoding="utf-8") as f:
        short_desc = f.read().strip()

    return {"version": version, "versionCode": version_code, "shortDesc": short_desc}


def replace_block(content, marker, new_inner, file_label):
    """Reemplaza el contenido entre <!-- BEGIN:marker --> y <!-- END:marker -->,
    preservando la indentación del marcador BEGIN para el END."""
    pattern = re.compile(
        rf"^([ \t]*)(<!--\s*BEGIN:{re.escape(marker)}\s*-->)(.*?)(<!--\s*END:{re.escape(marker)}\s*-->)",
        re.S | re.M,
    )
    if not pattern.search(content):
        die(f"Falta el marcador BEGIN:{marker}/END:{marker} en {file_label}.")
    new_inner_escaped = new_inner.replace("\\", "\\\\")
    return pattern.sub(rf"\1\2\n{new_inner_escaped}\n\1\4", content)


MAX_IMG_HEIGHT = 800  # px: alto maximo de las capturas servidas por el sitio


def prepare_image_bytes(src_path):
    """Devuelve los bytes finales de una captura (reescalada si supera MAX_IMG_HEIGHT)."""
    with open(src_path, "rb") as f:
        raw = f.read()
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw))
        if img.height > MAX_IMG_HEIGHT:
            w = round(img.width * MAX_IMG_HEIGHT / img.height)
            img = img.resize((w, MAX_IMG_HEIGHT), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except ImportError:
        pass  # sin Pillow: copiar tal cual
    return raw


def sync_images(catalog, source_repo, apply_changes):
    """Copia las capturas del producto al sitio y arma el HTML de la galeria.

    Devuelve (gallery_html, thumbs_html, home_img_html, hubo_cambios)."""
    shots = catalog.get("screenshots") or []
    if not shots:
        return None, None, None, False

    src_dir = os.path.join(source_repo, "documents", "store-assets")
    dest_dir = os.path.join(SITE, catalog["path"], "img")
    alts = catalog.get("altTexts") or []
    hero_idx = catalog.get("heroIndex", 0)
    vc = catalog.get("versionCode")
    changed = False

    os.makedirs(dest_dir, exist_ok=True)
    wanted = set()
    print("Imagenes:")
    for i, name in enumerate(shots):
        src = os.path.join(src_dir, name)
        if not os.path.exists(src):
            die(f"No existe la captura fuente {src}.")
        data = prepare_image_bytes(src)
        dest = os.path.join(dest_dir, name)
        wanted.add(name)
        if os.path.exists(dest):
            with open(dest, "rb") as f:
                same = hashlib.sha256(f.read()).digest() == hashlib.sha256(data).digest()
            status = "IGUAL" if same else "CAMBIADA"
        else:
            status = "NUEVA"
        if status != "IGUAL":
            changed = True
            if apply_changes:
                with open(dest, "wb") as f:
                    f.write(data)
        print(f"  {name}: {status}")
        if i == hero_idx:
            # imagen.png = copia de la principal (compatibilidad con referencias viejas)
            legacy = os.path.join(SITE, catalog["path"], "imagen.png")
            legacy_same = (os.path.exists(legacy) and
                           hashlib.sha256(open(legacy, "rb").read()).digest() ==
                           hashlib.sha256(data).digest())
            if not legacy_same:
                changed = True
                if apply_changes:
                    with open(legacy, "wb") as f:
                        f.write(data)
            print(f"  imagen.png (= {name}): {'IGUAL' if legacy_same else 'ACTUALIZADA'}")

    # limpiar huerfanas
    for f in sorted(os.listdir(dest_dir)):
        if f.lower().endswith(".png") and f not in wanted:
            changed = True
            print(f"  {f}: ELIMINADA (ya no esta en el catalogo)")
            if apply_changes:
                os.remove(os.path.join(dest_dir, f))

    def alt(i):
        return html_mod.escape(alts[i]) if i < len(alts) else \
            html_mod.escape(f"{catalog['nombre']} — captura {i + 1}")

    slides, thumbs = [], []
    for i, name in enumerate(shots):
        active = " active" if i == 0 else ""
        slides.append(f'        <img class="gal-slide{active}" src="img/{name}?v={vc}" alt="{alt(i)}">')
        thumbs.append(
            f'        <button class="gal-thumb{active}" type="button" aria-label="{alt(i)}">'
            f'<img src="img/{name}?v={vc}" alt=""></button>')

    hero_name = shots[hero_idx] if hero_idx < len(shots) else shots[0]
    home_img = (f'        <img src="./{catalog["path"]}/img/{hero_name}?v={vc}" '
                f'alt="{alt(hero_idx)}">')
    return "\n".join(slides), "\n".join(thumbs), home_img, changed


def build_store_links(catalog):
    """Genera el bloque de botones de stores + video segun los links del catalogo."""
    parts = ['    <div class="cta-row">']
    if catalog.get("playStoreUrl"):
        parts.append(
            f'      <a class="btn-primary" href="{catalog["playStoreUrl"]}" target="_blank" rel="noopener">\n'
            '        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>\n'
            '        Google Play Store\n'
            '      </a>'
        )
    if catalog.get("appStoreUrl"):
        parts.append(
            f'      <a class="btn-primary" href="{catalog["appStoreUrl"]}" target="_blank" rel="noopener">App Store</a>'
        )
    if catalog.get("youtubeUrl"):
        parts.append(
            f'      <a class="store-btn" href="{catalog["youtubeUrl"]}" target="_blank" rel="noopener">▶ Ver trailer</a>'
        )
    parts.append('    </div>')
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--source", help="Ruta al repo del producto (default: sourceRepo del catalogo)")
    ap.add_argument("--youtube-url", help="Registrar/actualizar la URL del video de YouTube")
    ap.add_argument("--apply", action="store_true", help="Escribir los cambios (default: dry-run)")
    args = ap.parse_args()

    catalog, catalog_path = load_catalog(args.slug)
    source_repo = args.source or catalog.get("sourceRepo")
    if not source_repo:
        die("Sin sourceRepo en el catalogo y sin --source.")

    if args.youtube_url:
        catalog["youtubeUrl"] = args.youtube_url

    data = read_source_data(source_repo, catalog)

    # Actualizar catalogo (en memoria)
    catalog["version"] = data["version"]
    catalog["versionCode"] = data["versionCode"]
    catalog["lastSyncDate"] = datetime.date.today().isoformat()

    desc_html = f'    <p class="game-desc">{html_mod.escape(data["shortDesc"], quote=False)}</p>'
    release_comment = (f'<!-- release: v{data["version"]} (vc{data["versionCode"]}) '
                       f'sync {catalog["lastSyncDate"]} -->')
    store_links_html = build_store_links(catalog)

    # Capturas: copiar al sitio + armar la galeria (usa el versionCode ya actualizado)
    gallery_html, thumbs_html, home_img_html, imgs_changed = sync_images(
        catalog, source_repo, args.apply)

    changed_any = imgs_changed

    # Paginas del producto
    for rel in catalog["pageFiles"]:
        path = os.path.join(SITE, rel)
        if not os.path.exists(path):
            die(f"No existe {rel} (listado en pageFiles).")
        with open(path, encoding="utf-8") as f:
            original = f.read()
        updated = original
        updated = replace_block(updated, "descripcion", desc_html, rel)
        updated = replace_block(updated, "store-links", store_links_html, rel)
        updated = replace_block(updated, "release-info", release_comment, rel)
        if gallery_html is not None:
            updated = replace_block(updated, "gallery", gallery_html, rel)
            updated = replace_block(updated, "gallery-thumbs", thumbs_html, rel)

        if updated != original:
            changed_any = True
            diff = difflib.unified_diff(
                original.splitlines(), updated.splitlines(),
                fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="", n=2)
            print("\n".join(diff))
            print()
            if args.apply:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(updated)

    # Home: descripcion de la tarjeta del producto
    home = os.path.join(SITE, "index.html")
    with open(home, encoding="utf-8") as f:
        original = f.read()
    home_desc = f'        <p class="game-desc">{html_mod.escape(data["shortDesc"], quote=False)}</p>'
    updated = replace_block(original, f"product-desc:{args.slug}", home_desc, "index.html")
    if home_img_html is not None:
        updated = replace_block(updated, f"product-image:{args.slug}", home_img_html, "index.html")
    if updated != original:
        changed_any = True
        diff = difflib.unified_diff(
            original.splitlines(), updated.splitlines(),
            fromfile="a/index.html", tofile="b/index.html", lineterm="", n=2)
        print("\n".join(diff))
        print()
        if args.apply:
            with open(home, "w", encoding="utf-8", newline="\n") as f:
                f.write(updated)

    # Catalogo
    with open(catalog_path, encoding="utf-8") as f:
        original_cat = f.read()
    new_cat = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if new_cat != original_cat:
        changed_any = True
        diff = difflib.unified_diff(
            original_cat.splitlines(), new_cat.splitlines(),
            fromfile=f"a/catalog/{args.slug}.json", tofile=f"b/catalog/{args.slug}.json",
            lineterm="", n=2)
        print("\n".join(diff))
        print()
        if args.apply:
            with open(catalog_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_cat)

    if not changed_any:
        print("Sin cambios: el sitio ya refleja el estado actual del producto.")
    elif args.apply:
        print("APLICADO. Revisar con git diff y commitear/pushear para publicar.")
    else:
        print("DRY-RUN (no se escribio nada). Repetir con --apply para aplicar.")


if __name__ == "__main__":
    main()
