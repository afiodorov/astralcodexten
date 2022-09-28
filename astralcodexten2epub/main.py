#!/usr/bin/env python

import dataclasses
import html
import json
import re
from dataclasses import dataclass
from itertools import count
from pathlib import Path
from typing import List

import requests
from bs4 import BeautifulSoup
from ebooklib import epub
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter, Retry


@dataclass
class Record:
    title: str
    canonical_url: str

    def skip(self) -> bool:
        title = self.title.lower()
        if "open thread" in title:
            return True

        if "links for" in title:
            return True

        if "mantic monday" in title:
            return True

        if "berkeley meetup" in title:
            return True

        return False


def pluck(d) -> Record:
    return Record(title=d["title"], canonical_url=d["canonical_url"])


def extract(page):
    soup = BeautifulSoup(page)
    el = soup.find("div", class_="available-content")
    return str(el)


notallowed = re.compile("[^_a-z0-9]+")


def get_fname(title, ext=".html"):
    fname = f"{title}".replace(" ", "_").lower()
    fname = notallowed.sub("", fname)
    return f"{fname}{ext}"


if __name__ == "__main__":
    s = requests.Session()
    retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retries))

    articles_list_file = Path("articles.json")
    if articles_list_file.exists():
        results_dicts = json.loads(articles_list_file.read_text(encoding="utf-8"))
        results = [Record(**x) for x in results_dicts]
    else:
        results: List[Record] = []

        for offset in count(start=0, step=12):
            resp = s.get(
                f"https://astralcodexten.substack.com/api/v1/archive?sort=new&search=&offset={offset}&limit=12"
            ).json()
            if len(resp) == 0:
                break

            results.extend([pluck(x) for x in resp])

        articles_list_file.write_text(json.dumps([dataclasses.asdict(x) for x in results]), encoding="utf-8")

    results = [x for x in results if not x.skip()]

    dest = Path("./articles")

    for x in results:
        fout = dest / get_fname(x.title)
        if fout.exists():
            continue

        fout.parent.mkdir(parents=True, exist_ok=True)
        res = extract(s.get(x.canonical_url).text)

        with fout.open(mode="w", encoding="utf-8") as f:
            f.write(res)

    new_dest = Path("./articles_processed")

    for x in results:
        text = (dest / get_fname(x.title)).read_text(encoding="utf-8")

        if len(text) < 200:
            continue

        target = new_dest / get_fname(x.title)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open(mode="w", encoding="utf-8") as f:
            soup = BeautifulSoup(
                f"""
            <!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
            <html xmlns="http://www.w3.org/1999/xhtml">
            <head>
            <title>
            {x.title}
            </title>
            </head>
            <body>
            <h1>{x.title}</h1>
            {text}
            </body>
            </html>
            """,
                "html.parser",
            )
            imgs = soup.find_all("img")

            for img in imgs:
                if img.previous_sibling is not None:
                    if img.previous_sibling.name == "source":
                        img.previous_sibling.decompose()

                img_name = Path(html.unescape(img["src"]))
                img_name = get_fname(Path(img_name.name).stem, ext=f"{img_name.suffix}")
                img_target = new_dest / img_name
                img_url = img["src"]
                img["src"] = "./" + img_name
                del img["srcset"]
                del img["data-attrs"]
                del img["width"]
                del img["height"]

                if img_target.exists():
                    continue

                with img_target.open(mode="wb") as img_f:
                    for chunk in s.get(img_url).iter_content():
                        img_f.write(chunk)

            for a in soup.findAll("a"):
                a.replaceWithChildren()

            f.write(str(soup.prettify()))

    for img_file in new_dest.glob("*"):
        if img_file.suffix == ".html":
            continue
        try:
            im = Image.open(img_file)
        except UnidentifiedImageError:
            continue

        format_ = im.format
        if im.size[0] > 250:
            new_width = 250
            new_height = im.size[1] * new_width / im.size[0]
            im = im.resize((new_width, int(new_height)), Image.Resampling.LANCZOS)
            im.save(img_file, format=format_)

    book = epub.EpubBook()
    book.set_identifier("astral-codex-ten")
    book.set_title("Astral Codex Ten")
    book.set_language("en")
    book.add_author("Scott Alexander")

    toc = []
    chapters = []

    for img_file in new_dest.glob("*"):
        if img_file.suffix == ".html":
            continue

        img = epub.EpubImage()
        img.set_content(img_file.read_bytes())
        img.file_name = img_file.name

        book.add_item(img)

    for x in results:
        c1 = epub.EpubHtml(title=x.title, file_name=get_fname(x.title), lang="en")
        chapter_file = new_dest / get_fname(x.title)
        if not chapter_file.exists():
            continue
        with chapter_file.open(encoding="utf-8") as f:
            c1.content = f.read()
        c1.id = get_fname(x.title)

        # add chapter
        book.add_item(c1)
        toc.append(epub.Link(get_fname(x.title), x.title, get_fname(x.title)))
        chapters.append(c1)

    book.toc = toc
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    style = "BODY {color: white;}"
    nav_css = epub.EpubItem(
        uid="style_nav", file_name="style/nav.css", media_type="text/css", content=style
    )
    book.add_item(nav_css)
    book.spine = ["nav"] + chapters
    epub.write_epub("astralcodexten.epub", book, {})
