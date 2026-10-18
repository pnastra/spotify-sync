#!/usr/bin/env python3
"""Run spotdl with YouTube Music searches in English instead of German.

spotdl 4.5.2 creates its YouTube Music client with language="de". With that setting
YouTube Music's "Songs" search returns nothing, so spotdl never sees official songs and
falls back to videos (live recordings, fan uploads). This launcher swaps in an English
client and then hands over to spotdl's normal command line; spotdl itself is not modified.

sync.py runs this with the Python interpreter of spotdl's own (pipx) environment, which is
where spotdl and ytmusicapi are installed. Remove it once spotdl fixes the issue upstream.
"""

import sys

from spotdl.console.entry_point import console_entry_point
from spotdl.providers.audio.ytmusic import YouTubeMusic
from ytmusicapi import YTMusic

YouTubeMusic._create_client = staticmethod(lambda: YTMusic(language="en"))

if __name__ == "__main__":
    sys.argv[0] = "spotdl"
    sys.exit(console_entry_point())
