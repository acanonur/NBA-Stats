-- This query aggregates NBA player stats by season, for every year on record,
-- and splits the totals between Regular Season and Playoffs.
-- Replace `<YOUR_TABLE_NAME>` with the actual table or view that stores
-- player-level game logs (one row per player per game), e.g.
-- `project.dataset.player_game_log`.
-- The table is expected to contain a `season` column (e.g. '2023-24') and a
-- `season_type` column (e.g. 'Regular Season' / 'Playoffs').
-- Written for BigQuery (Standard SQL): identifiers use backticks, not quotes.

SELECT
    season                                                   AS Season,
    season_type                                              AS Type,
    player_name                                              AS Player,
    team                                                     AS Team,
    COUNT(*)                                                 AS GP,
    SUM(started_game)                                        AS GS,
    ROUND(AVG(minutes_played), 1)                            AS MIN,
    ROUND(AVG(points), 1)                                    AS PTS,
    ROUND(AVG(rebounds), 1)                                  AS REB,
    ROUND(AVG(assists), 1)                                   AS AST,
    ROUND(AVG(steals), 1)                                    AS STL,
    ROUND(AVG(blocks), 1)                                    AS BLK,
    ROUND(AVG(turnovers), 1)                                 AS TOV,
    ROUND(AVG(field_goals_made), 1)                          AS FGM,
    ROUND(AVG(field_goals_attempted), 1)                     AS FGA,
    ROUND(100.0 * SUM(field_goals_made)
               / NULLIF(SUM(field_goals_attempted), 0), 1)   AS FG_PCT,
    ROUND(AVG(three_pointers_made), 1)                       AS THREE_PM,
    ROUND(AVG(three_pointers_attempted), 1)                  AS THREE_PA,
    ROUND(100.0 * SUM(three_pointers_made)
               / NULLIF(SUM(three_pointers_attempted), 0), 1) AS THREE_P_PCT,
    ROUND(AVG(free_throws_made), 1)                          AS FTM,
    ROUND(AVG(free_throws_attempted), 1)                     AS FTA,
    ROUND(100.0 * SUM(free_throws_made)
               / NULLIF(SUM(free_throws_attempted), 0), 1)   AS FT_PCT,
    ROUND(AVG(offensive_rebounds), 1)                        AS OREB,
    ROUND(AVG(defensive_rebounds), 1)                        AS DREB,
    ROUND(AVG(personal_fouls), 1)                            AS FOULS,
    ROUND(AVG(nba_fantasy_pts), 1)                           AS NBA_Pts
FROM
    `<YOUR_TABLE_NAME>`
GROUP BY
    season,
    season_type,
    player_name,
    team
-- Optional: filter by a specific player or season type
-- HAVING Player = 'Player Name'
-- HAVING Type = 'Playoffs'
ORDER BY
    Season DESC,
    Type,
    PTS DESC;
