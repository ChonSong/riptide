#!/bin/bash
# Riptide poller — runs every 2 min to find @riptide-bot fix comments on external repos
cd /home/sc/workspace/riptide
python3 -m riptide.poller 2>&1 | tee -a /home/sc/.local/share/riptide/poller.log
