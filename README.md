# RefGallery

Build a standalone HTML reference gallery from local images, web image URLs, or both. Requires Python 3.10 or later and a modern browser. No Python packages are required.

```sh
git clone https://github.com/tumayouzyo/RefGallery.git
cd RefGallery
python build_gallery.py --mode local --folder "/path/to/images" --title "References"
```

Open `Outputs/References_gallery.html` in your browser. Use `python3` or `py -3` if your installation uses a different Python command.

For web references, copy `gallery.example.json`, fill in your image URLs and source pages, then run:

```sh
python build_gallery.py --mode web --manifest my-gallery.json
```

See [INSTRUCTION.md](INSTRUCTION.md) for configuration, filtering, and updates, or [WEB-COLLECTION.md](WEB-COLLECTION.md) for AI-assisted web collection.
