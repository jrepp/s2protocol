#!/usr/bin/env python
#
# Copyright (c) 2013 Blizzard Entertainment
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import sys
import argparse
import pprint
import json
import binascii

from mpyq import mpyq
import protocol15405


class EventFilter(object):
    def process(self, event):
        """ Called for each event in the replay stream """
        return event

    def finish(self):
        """ Called when the stream has finished """
        pass


class JSONOutputFilter(EventFilter):
    """ Added as a filter will format the event into JSON """
    def __init__(self):
        EventFilter.__init__(self)

    def process(self, event):
        return json.dumps(event, ensure_ascii=False)


class PrettyPrintFilter(EventFilter):
    """ Add as a filter will send objects to stdout """
    def __init__(self, output):
        self._output = output

    def process(self, event):
        pprint.pprint(event, stream=self._output)
        return event


class TypeDumpFilter(EventFilter):
    """ Add as a filter to convert events into type information """
    def process(self, event):
        def recurse_into(value):
            if type(value) is list:
                decoded = []
                for item in value:
                    decoded.append(recurse_into(item))
                return decoded
            elif type(value) is dict:
                decoded = {}
                for key, inner_value in value.iteritems():
                    decoded[key] = recurse_into(inner_value)
                return decoded
            return (type(value).__name__, value)
        return recurse_into(event)
    

class StatCollectionFilter(EventFilter):
    """ Add as a filter to collect stats on events """
    def __init__(self):
        self._event_stats = {}

    def process(self, event):
        # update stats
        if '_event' in event and '_bits' in event:
            stat = self._event_stats.get(event['_event'], [0, 0])
            stat[0] += 1  # count of events
            stat[1] += event['_bits']  # count of bits
            self._event_stats[event['_event']] = stat
        return event

    def finish(self):
        print >> sys.stdout, 'Name, Count, Bits'
        for name, stat in sorted(self._event_stats.iteritems(), key=lambda x: x[1][1]):
            print >> sys.stdout, '"%s", %d, %d' % (name, stat[0], stat[1] / 8)


def convert_fourcc(fourcc_hex):
    """
    Convert a hexidecimal [fourcc](https://en.wikipedia.org/wiki/FourCC) 
    represpentation to a string.
    """
    s = []
    for i in xrange(0, 7, 2):
        n = int(fourcc_hex[i:i+2], 16)
        if n is not 0:
            s.append(chr(n))
    return ''.join(s)


def cache_handle_uri(handle):
    """
    Convert a 'cache handle' from a binary string to a string URI
    """
    handle_hex = binascii.b2a_hex(handle)
    purpose = convert_fourcc(handle_hex[0:8]) # first 4 bytes
    region = convert_fourcc(handle_hex[8:16]) # next 4 bytes
    content_hash = handle_hex[16:]
  
    uri = ''.join([
        'http://',
        region.lower(),
        '.depot.battle.net:1119/',
        content_hash.lower(), '.',
        purpose.lower()
      ])
    return uri


def process_init_data(initdata):
    translated_handles = []
    for handle in initdata['m_syncLobbyState']['m_gameDescription']['m_cacheHandles']:
        translated_handles.append(cache_handle_uri(handle))
    initdata['m_syncLobbyState']['m_gameDescription']['m_cacheHandles'] = translated_handles
    return initdata


def main():
    filters = []
    parser = argparse.ArgumentParser()
    parser.add_argument('replay_file', help='.SC2Replay file to load')
    parser.add_argument("--gameevents", help="print game events",
                        action="store_true")
    parser.add_argument("--messageevents", help="print message events",
                        action="store_true")
    parser.add_argument("--trackerevents", help="print tracker events",
                        action="store_true")
    parser.add_argument("--attributeevents", help="print attributes events",
                        action="store_true")
    parser.add_argument("--header", help="print protocol header",
                        action="store_true")
    parser.add_argument("--details", help="print protocol details",
                        action="store_true")
    parser.add_argument("--initdata", help="print protocol initdata",
                        action="store_true")
    parser.add_argument("--all", help="print all data",
                        action="store_true")
    parser.add_argument("--quiet", help="disable printing",
                        action="store_true")
    parser.add_argument("--stats", help="print stats",
                        action="store_true")
    parser.add_argument("--json", help="print output as json",
                        action="store_true")
    parser.add_argument("--types", help="show type information in event output",
                        action="store_true")
    args = parser.parse_args()

    archive = mpyq.MPQArchive(args.replay_file)
    
    filters = []
    if not args.quiet:
        filters.insert(0, PrettyPrintFilter(sys.stdout))

    if args.json:
        filters.insert(0, JSONOutputFilter())

    if args.types:
        filters.insert(0, TypeDumpFilter())

    if args.stats:
        filters.insert(0, StatCollectionFilter())

    def process_event(event):
        for f in filters:
            event = f.process(event)
        
    # Read the protocol header, this can be read with any protocol
    contents = archive.header['user_data_header']['content']
    header = protocol15405.decode_replay_header(contents)
    if args.header:
        process_event(args.header)

    # The header's baseBuild determines which protocol to use
    baseBuild = header['m_version']['m_baseBuild']
    try:
        protocol = __import__('protocol%s' % (baseBuild,))
    except:
        print >> sys.stderr, 'Unsupported base build: %d' % baseBuild
        sys.exit(1)
        
    # Print protocol details
    if args.all or args.details:
        contents = archive.read_file('replay.details')
        details = protocol.decode_replay_details(contents)
        process_event(details)

    # Print protocol init data
    if args.all or args.initdata:
        contents = archive.read_file('replay.initData')
        initdata = protocol.decode_replay_initdata(contents)
        initdata = process_init_data(initdata)
        process_event(initdata)

    # Print game events and/or game events stats
    if args.all or args.gameevents:
        contents = archive.read_file('replay.game.events')
        map(process_event, protocol.decode_replay_game_events(contents))

    # Print message events
    if args.all or args.messageevents:
        contents = archive.read_file('replay.message.events')
        map(process_event, protocol.decode_replay_message_events(contents))

    # Print tracker events
    if args.all or args.trackerevents:
        if hasattr(protocol, 'decode_replay_tracker_events'):
            contents = archive.read_file('replay.tracker.events')
            map(process_event, protocol.decode_replay_tracker_events(contents))

    # Print attributes events
    if args.all or args.attributeevents:
        contents = archive.read_file('replay.attributes.events')
        attributes = protocol.decode_replay_attributes_events(contents)
        process_event(attributes)
        
    for f in filters:
        f.finish()

if __name__ == '__main__':
    main()
