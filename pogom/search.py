#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import re
import json
import struct
import logging
import requests
import time
import s2sphere as s2
import math
from pgoapi import PGoApi
from pgoapi.utilities import f2i, h2f, get_cellid, encode, get_pos_by_name

from .utils import coords_of_cell
from . import config
from .models import parse_map, SearchConfig

log = logging.getLogger(__name__)

TIMESTAMP = '\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000\000'
REQ_SLEEP = 1
api = PGoApi()


def set_cover():
    coords = s2.LatLng(
        math.radians(SearchConfig.ORIGINAL_LATITUDE),
        math.radians(SearchConfig.ORIGINAL_LONGITUDE))

    # alternate form of 1-cos(asin(x))
    height = 1 - math.sqrt(1 - (float(SearchConfig.RADIUS)/6730000)**2 )

    cap = s2.Cap.from_axis_height(coords.to_point(), height)
    log.info(str(coords))

    coverer = s2.RegionCoverer()
    coverer.min_level = 16
    coverer.max_level = 16
    coverer.max_cells = 200

    cover = [s2.Cell(cell_id) for cell_id in coverer.get_covering(cap)]
    SearchConfig.COVER = cover


def set_location(location, radius):
    position = get_pos_by_name(location)
    log.info('Parsed location is: {:.4f}/{:.4f}/{:.4f} (lat/lng/alt)'.
             format(*position))

    SearchConfig.ORIGINAL_LATITUDE = position[0]
    SearchConfig.ORIGINAL_LONGITUDE = position[1]
    SearchConfig.RADIUS = radius


def send_map_request(api, position, args):
    try:
        login_if_necessary(args, position)
    
        api.set_position(*position)
        api.get_map_objects(latitude=f2i(position[0]),
                            longitude=f2i(position[1]),
                            since_timestamp_ms=TIMESTAMP,
                            cell_id=get_cellid(position[0], position[1]))
        return api.call()
    except Exception as e:  # make sure we dont crash in the main loop
        log.exception("Uncaught exception when downloading map")
        return False


def generate_location_steps():
    for cover in SearchConfig.COVER:
        coords = coords_of_cell(cover)
        yield (coords["lat"], coords["lng"], 0)


def login(args, position):
    log.info('Attempting login')

    api.set_position(*position)

    while not api.login(args.auth_service, args.username, args.password):
        log.info('Login failed, retrying')
        time.sleep(REQ_SLEEP)

    log.info('Login successful')

def login_if_necessary(args, position):
    if api._auth_provider and api._auth_provider._ticket_expire:
        remaining_time = api._auth_provider._ticket_expire/1000 - time.time()

        if remaining_time < 60:
            log.info("Login has or is about to expire")
            login(args, position)
    else:
        login(args, position)


def search(args):
    num_steps = len(SearchConfig.COVER)
    position = (SearchConfig.ORIGINAL_LATITUDE, SearchConfig.ORIGINAL_LONGITUDE, 0)

    log.info("search")

    i = 1
    for step_location in generate_location_steps():
        log.info('Scanning step {:d} of {:d}.'.format(i, num_steps))
        log.debug('Scan location is {:f}, {:f}'.format(step_location[0], step_location[1]))

        response_dict = send_map_request(api, step_location, args)
        while not response_dict:
            log.info('Map Download failed. Trying again.')
            response_dict = send_map_request(api, step_location, args)
            time.sleep(REQ_SLEEP)

        try:
            parse_map(response_dict)
        except KeyError:
            log.exception('Failed to parse response')

        log.info('Completed {:5.2f}% of scan.'.format(float(i) / num_steps*100))
        i += 1
        time.sleep(REQ_SLEEP)


def search_loop(args):
    while True:
        search(args)
        log.info("Finished scan. Sleeping 30s.")
        time.sleep(30)
