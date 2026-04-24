-- This query aggregates NBA player stats by season, for every year on record,
-- and splits the totals between Regular Season and Playoffs.
-- Replace `<YOUR_TABLE_NAME>` with the actual table or view that stores
-- player-level game logs (one row per player per game).
-- The table is expected to contain a `season` column (e.g. '2023-24') and a
-- `season_type` column (e.g. 'Regular Season' / 'Playoffs').

SELECT
    season                                                   AS "Season",
    season_type                                              AS "Type",
    player_name                                              AS "Player",
    team                                                     AS "Team",
    COUNT(*)                                                 AS "GP",
    SUM(started_game)                                        AS "GS",
    ROUND(AVG(minutes_played), 1)                            AS "MIN",
    ROUND(AVG(points), 1)                                    AS "PTS",
    ROUND(AVG(rebounds), 1)                                  AS "REB",
    ROUND(AVG(assists), 1)                                   AS "AST",
    ROUND(AVG(steals), 1)                                    AS "STL",
    ROUND(AVG(blocks), 1)                                    AS "BLK",
    ROUND(AVG(turnovers), 1)                                 AS "TO",
    ROUND(AVG(field_goals_made), 1)                          AS "FGM",
    ROUND(AVG(field_goals_attempted), 1)                     AS "FGA",
    ROUND(100.0 * SUM(field_goals_made)
               / NULLIF(SUM(field_goals_attempted), 0), 1)   AS "FG%",
    ROUND(AVG(three_pointers_made), 1)                       AS "3PM",
    ROUND(AVG(three_pointers_attempted), 1)                  AS "3PA",
    ROUND(100.0 * SUM(three_pointers_made)
               / NULLIF(SUM(three_pointers_attempted), 0), 1) AS "3P%",
    ROUND(AVG(free_throws_made), 1)                          AS "FTM",
    ROUND(AVG(free_throws_attempted), 1)                     AS "FTA",
    ROUND(100.0 * SUM(free_throws_made)
               / NULLIF(SUM(free_throws_attempted), 0), 1)   AS "FT%",
    ROUND(AVG(offensive_rebounds), 1)                        AS "OREB",
    ROUND(AVG(defensive_rebounds), 1)                        AS "DREB",
    ROUND(AVG(personal_fouls), 1)                            AS "FOULS",
    ROUND(AVG(nba_fantasy_pts), 1)                           AS "NBA Pts"
FROM
    <YOUR_TABLE_NAME>
GROUP BY
    season,
    season_type,
    player_name,
    team
-- Optional: filter by a specific player or season type
-- HAVING player_name = 'Player Name'
-- HAVING season_type = 'Playoffs'
ORDER BY
    season DESC,
    season_type,
    "PTS" DESC;
