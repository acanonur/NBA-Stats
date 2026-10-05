import Foundation

/// Small invented payloads for previews and for tests that want a value without a fixture file.
///
/// WHY HAND-WRITTEN AND SMALL
/// The recorded fixtures (`contracts/fixtures/leagues/**`) are 50 to 80 KB each, far too large to
/// embed in source. These are the same shapes with two or three of everything. Every club
/// (ZZA to ZZE), player, venue and link is invented, the same look-alike rule the committed
/// fixtures follow, and every payload says `isDemo: true` in its freshness block, so a preview
/// shows the demo banner exactly as the app does for an invented league. Nothing in them is a
/// real person's availability or a real game.
///
/// A decoder drops a key its struct does not know without a word, and `try?` turns a failed decode
/// into the empty fallback, so a typo here would be a blank card nobody noticed.
/// `LeaguePayloadDecodingTests` therefore decodes each literal and checks values that only survive
/// when the keys are right, and the literals were checked key by key against the structs with the
/// same walker `scripts/check_swift_portability.py` uses for the recorded fixtures (P14).
enum LeaguePreviewData {

    /// Decodes a JSON literal with the app's shared decoder, or nil when it does not decode.
    static func decode<T: Decodable>(_ type: T.Type, json: String) -> T? {
        guard let data = json.data(using: .utf8) else { return nil }
        return try? APIClient.makeDecoder().decode(type, from: data)
    }

    /// A matchup between two invented clubs, early in a season.
    static let matchupJSON = #"""
{
 "league": "euroleague",
 "season": "2026-27",
 "seasonCode": "E2026",
 "seasonType": null,
 "phase": null,
 "freshness": {
  "league": "euroleague",
  "syncVersion": 1,
  "dataThrough": "2026-10-02",
  "generatedAt": "2026-10-03T09:00:00Z",
  "isDemo": true,
  "sources": [
   {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
   {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
  ]
 },
 "game": {
  "league": "euroleague",
  "gameId": "E2026-R03-01",
  "date": "2026-10-08",
  "tipoffUtc": "2026-10-08T18:30:00Z",
  "venue": "Demo Arena",
  "isNeutral": false,
  "round": 3,
  "phase": "RS",
  "status": "scheduled",
  "home": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
  "away": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
  "homePts": null,
  "awayPts": null,
  "overtimePeriods": null
 },
 "teams": [
  {
   "side": "home",
   "team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
   "record": {"wins": 1, "losses": 1},
   "games": 2,
   "pointsPerGame": 85.0,
   "pointsAllowedPerGame": 83.0,
   "differentialPerGame": 2.0,
   "pointsPerRegulation": 85.0,
   "pointsAllowedPerRegulation": 83.0,
   "latestGame": {
    "gameId": "E2026-R02-03",
    "date": "2026-10-02",
    "opponent": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null},
    "isHome": false,
    "isNeutral": false,
    "teamScore": 82,
    "opponentScore": 86,
    "result": "L",
    "overtimePeriods": null
   },
   "form": [
    {
     "gameId": "E2026-R02-03",
     "date": "2026-10-02",
     "opponent": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null},
     "isHome": false,
     "isNeutral": false,
     "teamScore": 82,
     "opponentScore": 86,
     "result": "L",
     "overtimePeriods": null
    },
    {
     "gameId": "E2026-R01-02",
     "date": "2026-09-25",
     "opponent": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
     "isHome": true,
     "isNeutral": false,
     "teamScore": 88,
     "opponentScore": 80,
     "result": "W",
     "overtimePeriods": null
    }
   ],
   "lastN": {"window": 5, "games": 2, "pointsPerGame": 85.0, "pointsAllowedPerGame": 83.0},
   "last10": {"window": 10, "games": 2, "pointsPerGame": 85.0, "pointsAllowedPerGame": 83.0},
   "venueSplits": {"home": {"games": 1, "pointsPerGame": 88.0, "pointsAllowedPerGame": 80.0}, "away": {"games": 1, "pointsPerGame": 82.0, "pointsAllowedPerGame": 86.0}, "neutral": null},
   "adjustedPointsAgainst": {"value": null, "games": 2},
   "adjustedPointsFor": {"value": null, "games": 2},
   "availability": {
    "out": 1,
    "doubtful": 0,
    "questionable": 0,
    "probable": 0,
    "keyAbsences": [
     {
      "player": {"league": "euroleague", "id": "demo-101", "name": "Tobin Marlowe", "position": "G", "positionRaw": "Guard", "jersey": "7", "headshotUrl": null, "personCode": "demo-101"},
      "status": "out",
      "chanceOfPlaying": 0.0,
      "expectedPointsLost": 6.2,
      "expectedMinutesLost": 24.5,
      "inForce": true,
      "isStale": false,
      "source": {
       "kind": "clubStatement",
       "label": "Alderwick statement",
       "url": "https://clubs.example.org/zza/statement-1",
       "publishedAt": "2026-10-02T12:00:00Z",
       "asOf": "2026-10-03T09:00:00Z",
       "fetchedAt": "2026-10-03T09:00:00Z",
       "snapshotId": null
      }
     }
    ],
    "freshnessState": "fresh",
    "asOf": "2026-10-03T09:00:00Z"
   },
   "defenseSummary": {
    "pointsAllowedPerGame": 83.0,
    "buckets": [
     {"position": "G", "pointsAllowedPerGame": 35.0, "deltaPerGame": 0.8, "band": null},
     {"position": "F", "pointsAllowedPerGame": 30.5, "deltaPerGame": -0.5, "band": null},
     {"position": "C", "pointsAllowedPerGame": 17.5, "deltaPerGame": 0.7, "band": null}
    ],
    "withheld": {"reason": "minimumGames", "message": "Only 2 games, too few to judge a defence"}
   }
  },
  {
   "side": "away",
   "team": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "record": {"wins": 2, "losses": 0},
   "games": 2,
   "pointsPerGame": 87.0,
   "pointsAllowedPerGame": 76.5,
   "differentialPerGame": 10.5,
   "pointsPerRegulation": 87.0,
   "pointsAllowedPerRegulation": 76.5,
   "latestGame": {
    "gameId": "E2026-R02-04",
    "date": "2026-10-02",
    "opponent": {"league": "euroleague", "id": "ZZE", "abbr": "ZZE", "name": "Eastmere Otters", "shortName": "Eastmere", "clubCode": "ZZE", "tvCode": null},
    "isHome": true,
    "isNeutral": false,
    "teamScore": 84,
    "opponentScore": 75,
    "result": "W",
    "overtimePeriods": null
   },
   "form": [
    {
     "gameId": "E2026-R02-04",
     "date": "2026-10-02",
     "opponent": {"league": "euroleague", "id": "ZZE", "abbr": "ZZE", "name": "Eastmere Otters", "shortName": "Eastmere", "clubCode": "ZZE", "tvCode": null},
     "isHome": true,
     "isNeutral": false,
     "teamScore": 84,
     "opponentScore": 75,
     "result": "W",
     "overtimePeriods": null
    },
    {
     "gameId": "E2026-R01-05",
     "date": "2026-09-26",
     "opponent": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
     "isHome": false,
     "isNeutral": false,
     "teamScore": 90,
     "opponentScore": 78,
     "result": "W",
     "overtimePeriods": null
    }
   ],
   "lastN": {"window": 5, "games": 2, "pointsPerGame": 87.0, "pointsAllowedPerGame": 76.5},
   "last10": {"window": 10, "games": 2, "pointsPerGame": 87.0, "pointsAllowedPerGame": 76.5},
   "venueSplits": {"home": {"games": 1, "pointsPerGame": 84.0, "pointsAllowedPerGame": 75.0}, "away": {"games": 1, "pointsPerGame": 90.0, "pointsAllowedPerGame": 78.0}, "neutral": null},
   "adjustedPointsAgainst": {"value": null, "games": 2},
   "adjustedPointsFor": {"value": null, "games": 2},
   "availability": {"out": 0, "doubtful": 0, "questionable": 0, "probable": 0, "keyAbsences": [], "freshnessState": "fresh", "asOf": "2026-10-03T09:00:00Z"},
   "defenseSummary": {
    "pointsAllowedPerGame": 76.5,
    "buckets": [
     {"position": "G", "pointsAllowedPerGame": 28.0, "deltaPerGame": -6.2, "band": null},
     {"position": "F", "pointsAllowedPerGame": 29.5, "deltaPerGame": -1.5, "band": null},
     {"position": "C", "pointsAllowedPerGame": 19.0, "deltaPerGame": 2.2, "band": null}
    ],
    "withheld": {"reason": "minimumGames", "message": "Only 2 games, too few to judge a defence"}
   }
  }
 ],
 "leagueAverage": {"pointsPerGame": 82.1, "teams": 20},
 "projection": {
  "league": "euroleague",
  "game": {
   "league": "euroleague",
   "gameId": "E2026-R03-01",
   "date": "2026-10-08",
   "tipoffUtc": "2026-10-08T18:30:00Z",
   "venue": "Demo Arena",
   "isNeutral": false,
   "round": 3,
   "phase": "RS",
   "status": "scheduled",
   "home": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
   "away": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "homePts": null,
   "awayPts": null,
   "overtimePeriods": null
  },
  "freshness": {
   "league": "euroleague",
   "syncVersion": 1,
   "dataThrough": "2026-10-02",
   "generatedAt": "2026-10-03T09:00:00Z",
   "isDemo": true,
   "sources": [
    {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
    {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
   ]
  },
  "home": {
   "team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
   "projectedPoints": 83.4,
   "range80": {"low": 71.2, "high": 95.6},
   "fullStrengthPoints": 87.1,
   "availabilityEffect": -3.7,
   "attackIndex": 1.01,
   "attackIndexAfterAvailability": 0.97,
   "defenceIndex": 1.02,
   "capBinding": false,
   "unassignedPoints": 0.0,
   "replacementPoints": 0.0,
   "keyAbsences": [
    {
     "player": {"league": "euroleague", "id": "demo-101", "name": "Tobin Marlowe", "position": "G", "positionRaw": "Guard", "jersey": "7", "headshotUrl": null, "personCode": "demo-101"},
     "status": "out",
     "chanceOfPlaying": 0.0,
     "expectedPointsLost": 6.2,
     "expectedMinutesLost": 24.5,
     "inForce": true,
     "isStale": false,
     "source": {
      "kind": "clubStatement",
      "label": "Alderwick statement",
      "url": "https://clubs.example.org/zza/statement-1",
      "publishedAt": "2026-10-02T12:00:00Z",
      "asOf": "2026-10-03T09:00:00Z",
      "fetchedAt": "2026-10-03T09:00:00Z",
      "snapshotId": null
     }
    }
   ]
  },
  "away": {
   "team": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "projectedPoints": 85.9,
   "range80": {"low": 73.7, "high": 98.1},
   "fullStrengthPoints": 85.9,
   "availabilityEffect": 0.0,
   "attackIndex": 1.03,
   "attackIndexAfterAvailability": 1.03,
   "defenceIndex": 0.97,
   "capBinding": false,
   "unassignedPoints": 0.0,
   "replacementPoints": 0.0,
   "keyAbsences": []
  },
  "margin": -2.5,
  "marginRange80": {"low": -14.7, "high": 9.7},
  "projectedWinner": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
  "isTossUp": false,
  "summary": "ZZB by 2.5",
  "combinedPoints": 169.3,
  "combinedAvailabilityEffect": -3.7,
  "homeAdvantagePoints": 2.5,
  "venueAssumed": false,
  "intervalBasis": "assumed",
  "model": {
   "key": "hardwood.team.v1",
   "version": "1",
   "kind": "latest",
   "computedAt": "2026-10-03T09:00:00Z",
   "inputsCutoff": "2026-10-03T09:00:00Z",
   "capPolicy": "consistent",
   "constants": [{"key": "homeAdvantagePoints", "value": 2.5, "provenance": "default", "isDefault": true, "description": null, "setAt": null}],
   "computedAfterTipoff": false
  },
  "assumptions": {"assumedAvailable": {"home": 11, "away": 12, "basis": "notOnSubmittedReport", "homeBasis": null, "awayBasis": null}, "staleEntriesIgnored": 0},
  "result": null,
  "availability": "full",
  "notes": ["Invented demo league: not real games."]
 },
 "availability": "full",
 "notes": ["Invented demo league: every club, player and score here is made up."]
}
"""#

    /// One invented club's points allowed by position, withheld after two games.
    static let defenseJSON = #"""
{
 "league": "euroleague",
 "season": "2026-27",
 "seasonCode": "E2026",
 "seasonType": null,
 "phase": null,
 "scheme": "gfc",
 "basis": "perGame",
 "regulationMinutes": 40,
 "freshness": {
  "league": "euroleague",
  "syncVersion": 1,
  "dataThrough": "2026-10-02",
  "generatedAt": "2026-10-03T09:00:00Z",
  "isDemo": true,
  "sources": [
   {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
   {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
  ]
 },
 "team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
 "window": {"kind": "season", "games": 2, "requested": null},
 "pointsAllowedPerGame": 83.0,
 "leaguePointsAllowedPerGame": 82.1,
 "buckets": [
  {
   "position": "G",
   "label": "Guards",
   "pointsAllowedPerGame": 35.0,
   "leagueAverage": 34.2,
   "deltaPerGame": 0.8,
   "opponentMinutesPerGame": 70.0,
   "pointsPerRegulationMinutes": null,
   "leagueRate": null,
   "share": 0.4217,
   "leagueShare": 0.4173,
   "rawIndex": null,
   "index": null,
   "standardError": null,
   "band": null
  },
  {
   "position": "F",
   "label": "Forwards",
   "pointsAllowedPerGame": 30.5,
   "leagueAverage": 31.0,
   "deltaPerGame": -0.5,
   "opponentMinutesPerGame": 66.0,
   "pointsPerRegulationMinutes": null,
   "leagueRate": null,
   "share": 0.3675,
   "leagueShare": 0.378,
   "rawIndex": null,
   "index": null,
   "standardError": null,
   "band": null
  },
  {
   "position": "C",
   "label": "Centers",
   "pointsAllowedPerGame": 17.5,
   "leagueAverage": 16.8,
   "deltaPerGame": 0.7,
   "opponentMinutesPerGame": 64.0,
   "pointsPerRegulationMinutes": null,
   "leagueRate": null,
   "share": 0.2108,
   "leagueShare": 0.2047,
   "rawIndex": null,
   "index": null,
   "standardError": null,
   "band": null
  }
 ],
 "provisional": true,
 "withheld": {"reason": "minimumGames", "message": "Only 2 games, too few to judge a defence"},
 "coverage": {"listed": 1.0, "workbookListing": 0.0, "unknown": 0.0},
 "reconciliation": {"sumOfBuckets": 83.0, "pointsAllowedPerGame": 83.0, "unreconciledGames": 0, "identity": "sum(deltaPerGame) = pointsAllowedPerGame - leaguePointsAllowedPerGame"},
 "method": {
  "minimumGames": 6,
  "provisionalBelowGames": 12,
  "coverageCeiling": 0.05,
  "shrinkage": "empiricalBayes",
  "leagueReliability": {"G": null, "F": null, "C": null},
  "leagueSignal": null,
  "bandRule": "A position shows better or worse only when the table is not provisional.",
  "positionSource": "the EuroLeague registration",
  "taxonomy": "Guard, Forward and Center, as each roster lists a player for the season.",
  "limitations": ["Positions are the ones a roster lists for the season, not who guarded whom on the night."]
 },
 "methodMessage": null,
 "availability": "full",
 "caveat": "Counts points scored by opposing players listed at each position. It does not measure who guarded whom.",
 "notes": ["Invented demo league: every club, player and score here is made up."]
}
"""#

    /// A fresh report with two sourced statuses and one club with no report.
    static let availabilityJSON = #"""
{
 "league": "euroleague",
 "asOf": "2026-10-03T09:00:00Z",
 "freshness": {
  "league": "euroleague",
  "syncVersion": 1,
  "dataThrough": "2026-10-02",
  "generatedAt": "2026-10-03T09:00:00Z",
  "isDemo": true,
  "sources": [
   {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
   {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
  ]
 },
 "state": "fresh",
 "message": "Statuses are researched from the linked sources and dated; none of them is a live feed.",
 "teams": [
  {
   "team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
   "reportState": "submitted",
   "game": null,
   "entries": [
    {
     "statusId": 1,
     "overrideId": null,
     "player": {"league": "euroleague", "id": "demo-101", "name": "Tobin Marlowe", "position": "G", "positionRaw": "Guard", "jersey": "7", "headshotUrl": null, "personCode": "demo-101"},
     "playerName": "Tobin Marlowe",
     "status": "out",
     "statusLabel": "Out",
     "chanceOfPlaying": 0.0,
     "modelStatus": null,
     "reasonCategory": "injury",
     "reasonText": "Invented ankle note",
     "expectedReturnText": "Two weeks",
     "expectedReturn": null,
     "game": null,
     "isOverride": false,
     "inForce": true,
     "outOfForceReason": null,
     "isStale": false,
     "ageMinutes": 840,
     "source": {
      "kind": "clubStatement",
      "label": "Alderwick statement",
      "url": "https://clubs.example.org/zza/statement-1",
      "publishedAt": "2026-10-02T12:00:00Z",
      "asOf": "2026-10-03T09:00:00Z",
      "fetchedAt": "2026-10-03T09:00:00Z",
      "snapshotId": null
     }
    },
    {
     "statusId": 2,
     "overrideId": null,
     "player": {"league": "euroleague", "id": "demo-102", "name": "Edric Voss", "position": "F", "positionRaw": "Forward", "jersey": "14", "headshotUrl": null, "personCode": "demo-102"},
     "playerName": "Edric Voss",
     "status": "questionable",
     "statusLabel": "Questionable",
     "chanceOfPlaying": 0.5,
     "modelStatus": null,
     "reasonCategory": "illness",
     "reasonText": "Invented illness note",
     "expectedReturnText": null,
     "expectedReturn": null,
     "game": null,
     "isOverride": false,
     "inForce": true,
     "outOfForceReason": null,
     "isStale": false,
     "ageMinutes": 300,
     "source": {
      "kind": "pressArticle",
      "label": "Demo Sports Daily",
      "url": "https://news.example.org/zza-voss",
      "publishedAt": "2026-10-02T21:00:00Z",
      "asOf": "2026-10-03T09:00:00Z",
      "fetchedAt": "2026-10-03T09:00:00Z",
      "snapshotId": null
     }
    }
   ]
  },
  {
   "team": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "reportState": "noReport",
   "game": null,
   "entries": []
  }
 ],
 "news": null,
 "attribution": "EuroLeague statistics from the EuroLeague's data service. Availability researched from the linked sources."
}
"""#

    /// A two-game slate of projected scores.
    static let slateJSON = #"""
{
 "league": "euroleague",
 "date": null,
 "round": 3,
 "freshness": {
  "league": "euroleague",
  "syncVersion": 1,
  "dataThrough": "2026-10-02",
  "generatedAt": "2026-10-03T09:00:00Z",
  "isDemo": true,
  "sources": [
   {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
   {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
  ]
 },
 "model": {
  "key": "hardwood.team.v1",
  "version": "1",
  "kind": "latest",
  "computedAt": "2026-10-03T09:00:00Z",
  "inputsCutoff": "2026-10-03T09:00:00Z",
  "capPolicy": "consistent",
  "computedAfterTipoff": null
 },
 "games": [
  {
   "league": "euroleague",
   "game": {
    "league": "euroleague",
    "gameId": "E2026-R03-01",
    "date": "2026-10-08",
    "tipoffUtc": "2026-10-08T18:30:00Z",
    "venue": "Demo Arena",
    "isNeutral": false,
    "round": 3,
    "phase": "RS",
    "status": "scheduled",
    "home": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
    "away": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
    "homePts": null,
    "awayPts": null,
    "overtimePeriods": null
   },
   "freshness": {
    "league": "euroleague",
    "syncVersion": 1,
    "dataThrough": "2026-10-02",
    "generatedAt": "2026-10-03T09:00:00Z",
    "isDemo": true,
    "sources": [
     {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
     {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
    ]
   },
   "home": {"team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null}, "projectedPoints": 83.4},
   "away": {"team": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null}, "projectedPoints": 85.9},
   "margin": -2.5,
   "projectedWinner": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "isTossUp": false,
   "summary": "ZZB by 2.5",
   "combinedPoints": 169.3,
   "combinedAvailabilityEffect": 0.0,
   "homeAdvantagePoints": 2.5,
   "venueAssumed": false,
   "intervalBasis": "assumed",
   "model": {
    "key": "hardwood.team.v1",
    "version": "1",
    "kind": "latest",
    "computedAt": "2026-10-03T09:00:00Z",
    "inputsCutoff": "2026-10-03T09:00:00Z",
    "capPolicy": "consistent",
    "constants": [{"key": "homeAdvantagePoints", "value": 2.5, "provenance": "default", "isDefault": true, "description": null, "setAt": null}],
    "computedAfterTipoff": false
   },
   "result": null,
   "availability": "full"
  },
  {
   "league": "euroleague",
   "game": {
    "league": "euroleague",
    "gameId": "E2026-R03-02",
    "date": "2026-10-08",
    "tipoffUtc": "2026-10-08T19:00:00Z",
    "venue": "Demo Arena",
    "isNeutral": false,
    "round": 3,
    "phase": "RS",
    "status": "scheduled",
    "home": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
    "away": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null},
    "homePts": null,
    "awayPts": null,
    "overtimePeriods": null
   },
   "freshness": {
    "league": "euroleague",
    "syncVersion": 1,
    "dataThrough": "2026-10-02",
    "generatedAt": "2026-10-03T09:00:00Z",
    "isDemo": true,
    "sources": [
     {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
     {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
    ]
   },
   "home": {"team": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null}, "projectedPoints": 86.2},
   "away": {"team": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null}, "projectedPoints": 80.7},
   "margin": 5.5,
   "projectedWinner": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
   "isTossUp": false,
   "summary": "ZZC by 5.5",
   "combinedPoints": 166.9,
   "combinedAvailabilityEffect": 0.0,
   "homeAdvantagePoints": 2.5,
   "venueAssumed": false,
   "intervalBasis": "assumed",
   "model": {
    "key": "hardwood.team.v1",
    "version": "1",
    "kind": "latest",
    "computedAt": "2026-10-03T09:00:00Z",
    "inputsCutoff": "2026-10-03T09:00:00Z",
    "capPolicy": "consistent",
    "constants": [{"key": "homeAdvantagePoints", "value": 2.5, "provenance": "default", "isDefault": true, "description": null, "setAt": null}],
    "computedAfterTipoff": false
   },
   "result": null,
   "availability": "full"
  }
 ],
 "review": null,
 "availability": "full",
 "notes": ["Invented demo league: every club, player and score here is made up."]
}
"""#

    /// A round of two projected games.
    static let roundJSON = #"""
{
 "league": "euroleague",
 "season": "2026-27",
 "seasonCode": "E2026",
 "round": 3,
 "phase": "RS",
 "freshness": {
  "league": "euroleague",
  "syncVersion": 1,
  "dataThrough": "2026-10-02",
  "generatedAt": "2026-10-03T09:00:00Z",
  "isDemo": true,
  "sources": [
   {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
   {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
  ]
 },
 "status": "upcoming",
 "games": [
  {
   "league": "euroleague",
   "game": {
    "league": "euroleague",
    "gameId": "E2026-R03-01",
    "date": "2026-10-08",
    "tipoffUtc": "2026-10-08T18:30:00Z",
    "venue": "Demo Arena",
    "isNeutral": false,
    "round": 3,
    "phase": "RS",
    "status": "scheduled",
    "home": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
    "away": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
    "homePts": null,
    "awayPts": null,
    "overtimePeriods": null
   },
   "freshness": {
    "league": "euroleague",
    "syncVersion": 1,
    "dataThrough": "2026-10-02",
    "generatedAt": "2026-10-03T09:00:00Z",
    "isDemo": true,
    "sources": [
     {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
     {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
    ]
   },
   "home": {"team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null}, "projectedPoints": 83.4},
   "away": {"team": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null}, "projectedPoints": 85.9},
   "margin": -2.5,
   "projectedWinner": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "isTossUp": false,
   "summary": "ZZB by 2.5",
   "combinedPoints": 169.3,
   "combinedAvailabilityEffect": 0.0,
   "homeAdvantagePoints": 2.5,
   "venueAssumed": false,
   "intervalBasis": "assumed",
   "model": {
    "key": "hardwood.team.v1",
    "version": "1",
    "kind": "latest",
    "computedAt": "2026-10-03T09:00:00Z",
    "inputsCutoff": "2026-10-03T09:00:00Z",
    "capPolicy": "consistent",
    "constants": [{"key": "homeAdvantagePoints", "value": 2.5, "provenance": "default", "isDefault": true, "description": null, "setAt": null}],
    "computedAfterTipoff": false
   },
   "result": null,
   "availability": "full",
   "marginRange80": {"low": -14.5, "high": 9.5},
   "notes": ["Players with no status in force are assumed to play."]
  },
  {
   "league": "euroleague",
   "game": {
    "league": "euroleague",
    "gameId": "E2026-R03-02",
    "date": "2026-10-08",
    "tipoffUtc": "2026-10-08T19:00:00Z",
    "venue": "Demo Arena",
    "isNeutral": false,
    "round": 3,
    "phase": "RS",
    "status": "scheduled",
    "home": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
    "away": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null},
    "homePts": null,
    "awayPts": null,
    "overtimePeriods": null
   },
   "freshness": {
    "league": "euroleague",
    "syncVersion": 1,
    "dataThrough": "2026-10-02",
    "generatedAt": "2026-10-03T09:00:00Z",
    "isDemo": true,
    "sources": [
     {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
     {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
    ]
   },
   "home": {"team": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null}, "projectedPoints": 86.2},
   "away": {"team": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null}, "projectedPoints": 80.7},
   "margin": 5.5,
   "projectedWinner": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
   "isTossUp": false,
   "summary": "ZZC by 5.5",
   "combinedPoints": 166.9,
   "combinedAvailabilityEffect": 0.0,
   "homeAdvantagePoints": 2.5,
   "venueAssumed": false,
   "intervalBasis": "assumed",
   "model": {
    "key": "hardwood.team.v1",
    "version": "1",
    "kind": "latest",
    "computedAt": "2026-10-03T09:00:00Z",
    "inputsCutoff": "2026-10-03T09:00:00Z",
    "capPolicy": "consistent",
    "constants": [{"key": "homeAdvantagePoints", "value": 2.5, "provenance": "default", "isDefault": true, "description": null, "setAt": null}],
    "computedAfterTipoff": false
   },
   "result": null,
   "availability": "full",
   "marginRange80": {"low": -6.5, "high": 17.5},
   "notes": ["Players with no status in force are assumed to play."]
  }
 ],
 "summary": {
  "games": 2,
  "tossUps": 0,
  "closestGame": {
   "league": "euroleague",
   "gameId": "E2026-R03-01",
   "date": "2026-10-08",
   "tipoffUtc": "2026-10-08T18:30:00Z",
   "venue": "Demo Arena",
   "isNeutral": false,
   "round": 3,
   "phase": "RS",
   "status": "scheduled",
   "home": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
   "away": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
   "homePts": null,
   "awayPts": null,
   "overtimePeriods": null
  },
  "averageCombinedPoints": 168.1,
  "homeWinnersProjected": 1,
  "awayWinnersProjected": 1
 },
 "notes": ["Invented demo league: every club, player and score here is made up."]
}
"""#

    /// One invented club with a two-player squad.
    static let clubJSON = #"""
{
 "league": "euroleague",
 "team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
 "season": "2026-27",
 "seasonCode": "E2026",
 "freshness": {
  "league": "euroleague",
  "syncVersion": 1,
  "dataThrough": "2026-10-02",
  "generatedAt": "2026-10-03T09:00:00Z",
  "isDemo": true,
  "sources": [
   {"key": "el.stats", "label": "EuroLeague statistics", "state": "ok", "reason": "Invented demo league: every game here is made up.", "lastSuccessAt": null},
   {"key": "el.manual", "label": "Entered by hand", "state": "ok", "reason": null, "lastSuccessAt": null}
  ]
 },
 "coach": "Demo Coach",
 "record": {"wins": 1, "losses": 1},
 "scoring": {
  "team": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
  "record": {"wins": 1, "losses": 1},
  "games": 2,
  "pointsPerGame": 85.0,
  "pointsAllowedPerGame": 83.0,
  "differentialPerGame": 2.0,
  "pointsPerRegulation": 85.0,
  "pointsAllowedPerRegulation": 83.0,
  "latestGame": {
   "gameId": "E2026-R02-03",
   "date": "2026-10-02",
   "opponent": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null},
   "isHome": false,
   "isNeutral": false,
   "teamScore": 82,
   "opponentScore": 86,
   "result": "L",
   "overtimePeriods": null
  },
  "form": [
   {
    "gameId": "E2026-R02-03",
    "date": "2026-10-02",
    "opponent": {"league": "euroleague", "id": "ZZD", "abbr": "ZZD", "name": "Dunmarrow Wolves", "shortName": "Dunmarrow", "clubCode": "ZZD", "tvCode": null},
    "isHome": false,
    "isNeutral": false,
    "teamScore": 82,
    "opponentScore": 86,
    "result": "L",
    "overtimePeriods": null
   },
   {
    "gameId": "E2026-R01-02",
    "date": "2026-09-25",
    "opponent": {"league": "euroleague", "id": "ZZC", "abbr": "ZZC", "name": "Cindervale Foxes", "shortName": "Cindervale", "clubCode": "ZZC", "tvCode": null},
    "isHome": true,
    "isNeutral": false,
    "teamScore": 88,
    "opponentScore": 80,
    "result": "W",
    "overtimePeriods": null
   }
  ],
  "lastN": {"window": 5, "games": 2, "pointsPerGame": 85.0, "pointsAllowedPerGame": 83.0},
  "last10": {"window": 10, "games": 2, "pointsPerGame": 85.0, "pointsAllowedPerGame": 83.0},
  "venueSplits": {"home": {"games": 1, "pointsPerGame": 88.0, "pointsAllowedPerGame": 80.0}, "away": {"games": 1, "pointsPerGame": 82.0, "pointsAllowedPerGame": 86.0}, "neutral": null},
  "adjustedPointsAgainst": {"value": null, "games": 2},
  "adjustedPointsFor": {"value": null, "games": 2},
  "availability": {
   "out": 1,
   "doubtful": 0,
   "questionable": 0,
   "probable": 0,
   "keyAbsences": [
    {
     "player": {"league": "euroleague", "id": "demo-101", "name": "Tobin Marlowe", "position": "G", "positionRaw": "Guard", "jersey": "7", "headshotUrl": null, "personCode": "demo-101"},
     "status": "out",
     "chanceOfPlaying": 0.0,
     "expectedPointsLost": 6.2,
     "expectedMinutesLost": 24.5,
     "inForce": true,
     "isStale": false,
     "source": {
      "kind": "clubStatement",
      "label": "Alderwick statement",
      "url": "https://clubs.example.org/zza/statement-1",
      "publishedAt": "2026-10-02T12:00:00Z",
      "asOf": "2026-10-03T09:00:00Z",
      "fetchedAt": "2026-10-03T09:00:00Z",
      "snapshotId": null
     }
    }
   ],
   "freshnessState": "fresh",
   "asOf": "2026-10-03T09:00:00Z"
  },
  "defenseSummary": {
   "pointsAllowedPerGame": 83.0,
   "buckets": [
    {"position": "G", "pointsAllowedPerGame": 35.0, "deltaPerGame": 0.8, "band": null},
    {"position": "F", "pointsAllowedPerGame": 30.5, "deltaPerGame": -0.5, "band": null},
    {"position": "C", "pointsAllowedPerGame": 17.5, "deltaPerGame": 0.7, "band": null}
   ],
   "withheld": {"reason": "minimumGames", "message": "Only 2 games, too few to judge a defence"}
  }
 },
 "rating": {
  "pfPrior": 84.0,
  "paPrior": 83.0,
  "priorIsEstimate": true,
  "attackAdj": 0.5,
  "defenceAdj": -0.2,
  "projectedPointsFor": 84.5,
  "projectedPointsAgainst": 82.8,
  "attackIndex": 1.01,
  "defenceIndex": 1.02,
  "asOfRound": 2,
  "source": "workbook",
  "updateWeight": 0.2,
  "updateBasis": "gamesPlayed",
  "extrapolated": false
 },
 "squad": [
  {
   "player": {"league": "euroleague", "id": "demo-101", "name": "Tobin Marlowe", "position": "G", "positionRaw": "Guard", "jersey": "7", "headshotUrl": null, "personCode": "demo-101"},
   "positionWorkbook5": "PG",
   "age": 27,
   "role": "Starter",
   "basis": "syntheticDemo",
   "basisNote": null,
   "isEstimate": false,
   "projectedMinutes": 28.0,
   "per40": {"pts": 22.0, "reb": 3.0, "ast": 7.0, "fg3m": 1.4, "stl": 1.1, "blk": 0.3, "tov": 2.5},
   "perGame": {"min": 28.0, "pts": 15.4, "reb": 2.1, "ast": 4.9, "fg3m": 0.9, "stl": 0.7, "blk": 0.2, "tov": 1.6, "pir": null},
   "seasonAverages": {"games": 2, "min": 28.0, "pts": 16.5, "reb": 3.0, "ast": 5.5, "pir": 15.0, "fg2Pct": 0.52, "fg3Pct": 0.38, "ftPct": 0.8},
   "status": "out",
   "chanceOfPlaying": 0.0,
   "inForce": null,
   "isStale": null,
   "availabilitySource": null
  },
  {
   "player": {"league": "euroleague", "id": "demo-102", "name": "Edric Voss", "position": "F", "positionRaw": "Forward", "jersey": "14", "headshotUrl": null, "personCode": "demo-102"},
   "positionWorkbook5": "PF",
   "age": 25,
   "role": "Starter",
   "basis": "syntheticDemo",
   "basisNote": null,
   "isEstimate": false,
   "projectedMinutes": 24.0,
   "per40": {"pts": 18.0, "reb": 8.0, "ast": 2.0, "fg3m": 1.4, "stl": 1.1, "blk": 0.3, "tov": 2.5},
   "perGame": {"min": 24.0, "pts": 10.8, "reb": 4.8, "ast": 1.2, "fg3m": 0.9, "stl": 0.7, "blk": 0.2, "tov": 1.6, "pir": null},
   "seasonAverages": {"games": 2, "min": 24.0, "pts": 16.5, "reb": 3.0, "ast": 5.5, "pir": 15.0, "fg2Pct": 0.52, "fg3Pct": 0.38, "ftPct": 0.8},
   "status": "questionable",
   "chanceOfPlaying": 0.5,
   "inForce": null,
   "isStale": null,
   "availabilitySource": null
  }
 ],
 "absences": [
  {
   "player": {"league": "euroleague", "id": "demo-101", "name": "Tobin Marlowe", "position": "G", "positionRaw": "Guard", "jersey": "7", "headshotUrl": null, "personCode": "demo-101"},
   "status": "out",
   "chanceOfPlaying": 0.0,
   "expectedPointsLost": 6.2,
   "expectedMinutesLost": 24.5,
   "inForce": true,
   "isStale": false,
   "source": {
    "kind": "clubStatement",
    "label": "Alderwick statement",
    "url": "https://clubs.example.org/zza/statement-1",
    "publishedAt": "2026-10-02T12:00:00Z",
    "asOf": "2026-10-03T09:00:00Z",
    "fetchedAt": "2026-10-03T09:00:00Z",
    "snapshotId": null
   }
  }
 ],
 "nextGame": {
  "league": "euroleague",
  "gameId": "E2026-R03-01",
  "date": "2026-10-08",
  "tipoffUtc": "2026-10-08T18:30:00Z",
  "venue": "Demo Arena",
  "isNeutral": false,
  "round": 3,
  "phase": "RS",
  "status": "scheduled",
  "home": {"league": "euroleague", "id": "ZZA", "abbr": "ZZA", "name": "Alderwick Herons", "shortName": "Alderwick", "clubCode": "ZZA", "tvCode": null},
  "away": {"league": "euroleague", "id": "ZZB", "abbr": "ZZB", "name": "Brindlemoor Stags", "shortName": "Brindlemoor", "clubCode": "ZZB", "tvCode": null},
  "homePts": null,
  "awayPts": null,
  "overtimePeriods": null
 },
 "defenseSummary": {
  "pointsAllowedPerGame": 83.0,
  "buckets": [
   {"position": "G", "pointsAllowedPerGame": 35.0, "deltaPerGame": 0.8, "band": null},
   {"position": "F", "pointsAllowedPerGame": 30.5, "deltaPerGame": -0.5, "band": null},
   {"position": "C", "pointsAllowedPerGame": 17.5, "deltaPerGame": 0.7, "band": null}
  ],
  "withheld": {"reason": "minimumGames", "message": "Only 2 games, too few to judge a defence"}
 },
 "notes": ["Invented demo league: every club, player and score here is made up."]
}
"""#
}

public extension LeagueMatchup {
    /// A matchup between two invented clubs, early in a season.
    static let preview: LeagueMatchup = LeaguePreviewData.decode(LeagueMatchup.self, json: LeaguePreviewData.matchupJSON)
        ?? LeagueMatchup()
}

public extension LeagueDefenseDocument {
    /// One invented club's points allowed by position, withheld after two games.
    static let preview: LeagueDefenseDocument = LeaguePreviewData.decode(LeagueDefenseDocument.self, json: LeaguePreviewData.defenseJSON)
        ?? LeagueDefenseDocument()
}

public extension LeagueAvailabilityReport {
    /// A fresh report with two sourced statuses and one club with no report.
    static let preview: LeagueAvailabilityReport = LeaguePreviewData.decode(LeagueAvailabilityReport.self, json: LeaguePreviewData.availabilityJSON)
        ?? LeagueAvailabilityReport()
}

public extension LeagueSlateProjections {
    /// A two-game slate of projected scores.
    static let preview: LeagueSlateProjections = LeaguePreviewData.decode(LeagueSlateProjections.self, json: LeaguePreviewData.slateJSON)
        ?? LeagueSlateProjections()
}

public extension ElRoundView {
    /// A round of two projected games.
    static let preview: ElRoundView = LeaguePreviewData.decode(ElRoundView.self, json: LeaguePreviewData.roundJSON)
        ?? ElRoundView()
}

public extension ElClubView {
    /// One invented club with a two-player squad.
    static let preview: ElClubView = LeaguePreviewData.decode(ElClubView.self, json: LeaguePreviewData.clubJSON)
        ?? ElClubView()
}
