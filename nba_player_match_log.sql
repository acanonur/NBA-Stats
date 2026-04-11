-- This query retrieves the daily game log for each match for each NBA player.
-- You will need to replace `<YOUR_TABLE_NAME>` with the actual name of the table
-- or view where your player match stats are stored.
-- If your data is spread across multiple tables (e.g., players, games, stats),
-- you will need to JOIN them and replace this placeholder accordingly.

SELECT
    game_date AS "Date",
    opponent AS "OPP",
    game_score AS "Score",
    nba_fantasy_pts AS "NBA Pts",
    started_game AS "START",
    shot_chart_link AS "Shot Chart",
    minutes_played AS "MIN",
    points AS "PTS",
    rebounds AS "REB",
    assists AS "AST",
    steals AS "STL",
    blocks AS "BLK",
    turnovers AS "TO",
    field_goals_made AS "FGM",
    field_goals_attempted AS "FGA",
    free_throws_made AS "FTM",
    free_throws_attempted AS "FTA",
    three_pointers_made AS "3PM",
    three_pointers_attempted AS "3PA",
    offensive_rebounds AS "OREB",
    defensive_rebounds AS "DREB",
    personal_fouls AS "FOULS"
FROM
    <YOUR_TABLE_NAME>
-- Optional: filter by a specific player or order by date
-- WHERE player_name = 'Player Name' OR player_id = 123
ORDER BY
    game_date DESC;
