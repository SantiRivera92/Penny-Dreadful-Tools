from decksite.view import View
from magic import tournaments


# pylint: disable=no-self-use
class TournamentLeaderboards(View):
    def __init__(self, series) -> None:
        super().__init__()
        self.series = series
        self.leaderboards = [s['entries'] for s in series] # These will be prepared in View.
        self.prizes = tournaments.prizes_by_finish(multiplier=3)
        self.show_seasons = True

    def page_title(self):
        return 'Tournament Leaderboards'
