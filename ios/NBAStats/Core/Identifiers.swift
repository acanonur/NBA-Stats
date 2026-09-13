import Foundation

/// The league's own player id (NBA.com `PERSON_ID`), carried verbatim through the API.
public typealias PlayerID = Int

/// The league's own franchise id (NBA.com `TEAM_ID`), carried verbatim through the API.
public typealias TeamID = Int

/// The league's own game id, a zero-padded string such as `"0022500512"`.
public typealias GameID = String
