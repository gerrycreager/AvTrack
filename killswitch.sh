#!/bin/bash
# Instantly disables the AvTrack proxy on r815 without touching WxCOP or anything
# else Apache serves. Safe to run any time.
set -e
sudo a2dissite avtrack
sudo systemctl reload apache2
echo 'AvTrack proxy disabled. Port 2468 will refuse connections (site removed from Apache).'
