import asyncio

from decksite import league
from magic import multiverse
from shared import dtutil
from shared import redis_wrapper as redis

from . import insert_seasons


def ad_hoc() -> None:
    try:
        event_loop = asyncio.get_event_loop()
    except RuntimeError:
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)

    league.set_status(league.Status.CLOSED)
    multiverse.init()  # New Cards?
    event_loop.run_until_complete(multiverse.set_legal_cards_async())  # PD current list
    event_loop.run_until_complete(multiverse.update_pd_legality_async())  # PD previous lists
    insert_seasons.run()  # Make sure Season table is up to date
    multiverse.rebuild_cache()
    if redis.REDIS:  # Clear the redis cache
        redis.REDIS.flushdb()
    league_end = league.active_league().end_date
    diff = league_end - dtutil.now()
    if diff.days > 0:
        league.set_status(league.Status.OPEN)
    print('Open the gates here')
    if redis.REDIS:  # Clear the redis cache
        redis.REDIS.flushdb()
