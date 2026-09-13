import Foundation
import XCTest
@testable import Hardwood

/// Widget configuration is schema-driven at runtime, so it travels as `JSONValue` rather than as
/// a fixed struct. Two properties matter more than the rest: `true` must never arrive as `1`
/// (a boolean toggle would become a number in the saved layout), and `10` must round-trip as an
/// integer so a saved dashboard re-encodes to the same bytes it was read from.
final class JSONValueTests: XCTestCase {

    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    private func decode(_ json: String) throws -> JSONValue {
        let data = try XCTUnwrap(json.data(using: .utf8))
        return try decoder.decode(JSONValue.self, from: data)
    }

    private func encodeToString(_ value: JSONValue) throws -> String {
        let data = try encoder.encode(value)
        return try XCTUnwrap(String(data: data, encoding: .utf8))
    }

    // MARK: - Discrimination

    func testBooleanIsNotAnInteger() throws {
        XCTAssertEqual(try decode(#"{"flag": true}"#)["flag"], .bool(true))
        XCTAssertEqual(try decode(#"{"flag": false}"#)["flag"], .bool(false))
        XCTAssertNotEqual(try decode(#"{"flag": true}"#)["flag"], .int(1))
    }

    func testIntegerIsNotADouble() throws {
        XCTAssertEqual(try decode(#"{"limit": 10}"#)["limit"], .int(10))
        XCTAssertNotEqual(try decode(#"{"limit": 10}"#)["limit"], .double(10))
    }

    func testFractionalNumberIsADouble() throws {
        XCTAssertEqual(try decode(#"{"minMinutes": 12.5}"#)["minMinutes"], .double(12.5))
    }

    func testNegativeAndLargeIntegers() throws {
        XCTAssertEqual(try decode(#"{"v": -7}"#)["v"], .int(-7))
        XCTAssertEqual(try decode(#"{"v": 1610612747}"#)["v"], .int(1_610_612_747))
    }

    func testStringAndNull() throws {
        XCTAssertEqual(try decode(#"{"season": "2025-26"}"#)["season"], .string("2025-26"))
        let document = try decode(#"{"subjectId": null}"#)
        let subjectId = try XCTUnwrap(document["subjectId"])
        XCTAssertEqual(subjectId, .null)
        XCTAssertTrue(subjectId.isNull)
    }

    // MARK: - Round trips

    func testScalarsRoundTrip() throws {
        let values: [JSONValue] = [
            .string("ts_pct"), .int(10), .double(12.5), .bool(true), .bool(false), .null
        ]
        for value in values {
            // Wrapped in an object: a bare scalar at the top level is a JSON fragment, which is a
            // different question from whether the case survives.
            let data = try encoder.encode(JSONValue.object(["v": value]))
            let decoded = try decoder.decode(JSONValue.self, from: data)
            XCTAssertEqual(decoded["v"], value, "\(value) did not survive a round trip")
        }
    }

    func testAWholeConfigurationRoundTrips() throws {
        let original: JSONValue = .object([
            "subjectType": .string("player"),
            "subjectId": .int(2544),
            "metric": .string("ts_pct"),
            "secondaryMetrics": .array([.string("pts"), .string("ast")]),
            "season": .string("2025-26"),
            "showSparkline": .bool(true),
            "sparklineWindow": .int(10),
            "minMinutes": .double(12.5),
            "note": .null
        ])
        let data = try encoder.encode(original)
        XCTAssertEqual(try decoder.decode(JSONValue.self, from: data), original)
    }

    /// The fixtures re-encode equivalently because `10` stays an integer rather than becoming
    /// `10.0`. Everything is wrapped in an object: a top-level fragment is a different question.
    func testAnIntegerReEncodesWithoutADecimalPoint() throws {
        let value = try decode(#"{"limit": 10}"#)
        XCTAssertEqual(value["limit"], .int(10))
        let text = try encodeToString(value)
        XCTAssertTrue(text.contains("10"), text)
        XCTAssertFalse(text.contains("10.0"), "An integer was re-encoded as a double: \(text)")
    }

    func testABooleanReEncodesAsABoolean() throws {
        let text = try encodeToString(try decode(#"{"flag": true}"#))
        XCTAssertTrue(text.contains("true"), text)
        XCTAssertFalse(text.contains("1"), "A boolean was re-encoded as a number: \(text)")
    }

    func testNullReEncodesAsNull() throws {
        let text = try encodeToString(.object(["nothing": .null]))
        XCTAssertTrue(text.contains("null"), text)
    }

    // MARK: - Nesting

    func testNestedObjectsAndArrays() throws {
        let json = """
        {
          "a": { "b": { "c": [1, 2, {"d": "deep"}] } },
          "list": [true, null, 3.5, "four"]
        }
        """
        let value = try decode(json)
        XCTAssertEqual(value["a"]?["b"]?["c"]?[0], .int(1))
        XCTAssertEqual(value["a"]?["b"]?["c"]?[2]?["d"], .string("deep"))
        XCTAssertEqual(value["list"]?[0], .bool(true))
        XCTAssertEqual(value["list"]?[1], .null)
        XCTAssertEqual(value["list"]?[2], .double(3.5))
        XCTAssertEqual(value["list"]?[3], .string("four"))
        // And it survives a round trip with the nesting intact.
        let data = try encoder.encode(value)
        XCTAssertEqual(try decoder.decode(JSONValue.self, from: data), value)
    }

    func testSubscriptsReturnNilRatherThanTrapping() throws {
        let value = try decode(#"{"list": [1, 2]}"#)
        XCTAssertNil(value["missing"])
        XCTAssertNil(value[0])
        XCTAssertNil(value["list"]?[9])
        XCTAssertNil(value["list"]?[-1])
        XCTAssertNil(value["list"]?["not an index"])
    }

    // MARK: - Accessors

    func testAccessorsReturnOnlyWhatTheyShould() {
        XCTAssertEqual(JSONValue.string("x").stringValue, "x")
        XCTAssertNil(JSONValue.int(1).stringValue)

        XCTAssertEqual(JSONValue.int(12).intValue, 12)
        XCTAssertEqual(JSONValue.double(12).intValue, 12, "An exactly representable double is an integer")
        XCTAssertNil(JSONValue.double(12.5).intValue)
        XCTAssertNil(JSONValue.string("12").intValue)

        XCTAssertEqual(JSONValue.double(12.5).doubleValue, 12.5)
        XCTAssertEqual(JSONValue.int(12).doubleValue, 12, "An integer reads as a double too")
        XCTAssertNil(JSONValue.bool(true).doubleValue)

        XCTAssertEqual(JSONValue.bool(true).boolValue, true)
        XCTAssertEqual(JSONValue.int(1).boolValue, true, "A 0/1 flag is tolerated")
        XCTAssertEqual(JSONValue.int(0).boolValue, false)
        XCTAssertNil(JSONValue.int(2).boolValue)
        XCTAssertNil(JSONValue.string("true").boolValue)

        XCTAssertEqual(JSONValue.array([.int(1)]).arrayValue, [.int(1)])
        XCTAssertNil(JSONValue.int(1).arrayValue)
        XCTAssertEqual(JSONValue.object(["a": .int(1)]).objectValue, ["a": .int(1)])
        XCTAssertNil(JSONValue.int(1).objectValue)

        XCTAssertTrue(JSONValue.null.isNull)
        XCTAssertFalse(JSONValue.int(0).isNull, "Zero is a value, not an absence")
    }

    func testTypedArrayAccessors() {
        XCTAssertEqual(JSONValue.array([.string("a"), .string("b")]).stringArrayValue, ["a", "b"])
        XCTAssertNil(JSONValue.array([.string("a"), .int(1)]).stringArrayValue)
        XCTAssertNil(JSONValue.string("a").stringArrayValue)
        XCTAssertEqual(JSONValue.array([.int(1), .int(2)]).intArrayValue, [1, 2])
        XCTAssertNil(JSONValue.array([.int(1), .string("b")]).intArrayValue)
    }

    // MARK: - Literals

    func testLiteralConformances() {
        let string: JSONValue = "ts_pct"
        let int: JSONValue = 10
        let double: JSONValue = 12.5
        let bool: JSONValue = true
        let null: JSONValue = nil
        let array: JSONValue = ["a", 1]
        let object: JSONValue = ["metric": "ts_pct", "limit": 10]

        XCTAssertEqual(string, .string("ts_pct"))
        XCTAssertEqual(int, .int(10))
        XCTAssertEqual(double, .double(12.5))
        XCTAssertEqual(bool, .bool(true))
        XCTAssertEqual(null, .null)
        XCTAssertEqual(array, .array([.string("a"), .int(1)]))
        XCTAssertEqual(object, .object(["metric": .string("ts_pct"), "limit": .int(10)]))
    }

    // MARK: - Description and bridging

    /// The description sorts object keys, which is what makes a cache key stable across two
    /// dictionaries that differ only in ordering.
    func testDescriptionSortsObjectKeys() {
        let first: JSONValue = .object(["b": .int(2), "a": .int(1)])
        let second: JSONValue = .object(["a": .int(1), "b": .int(2)])
        XCTAssertEqual(first.description, second.description)
        XCTAssertEqual(first.description, "{a: 1, b: 2}")
    }

    func testBridgesFromJSONSerialization() throws {
        let json = #"{"flag": true, "n": 1, "x": 1.5, "s": "text", "list": [1, 2], "nothing": null}"#
        let data = try XCTUnwrap(json.data(using: .utf8))
        let object = try JSONSerialization.jsonObject(with: data)
        let bridged = try XCTUnwrap(JSONValue(any: object))
        XCTAssertEqual(bridged["flag"], .bool(true), "CFBoolean must not arrive as a number")
        XCTAssertEqual(bridged["n"], .int(1))
        XCTAssertEqual(bridged["x"], .double(1.5))
        XCTAssertEqual(bridged["s"], .string("text"))
        XCTAssertEqual(bridged["list"], .array([.int(1), .int(2)]))
        XCTAssertEqual(bridged["nothing"], .null)
    }

    func testBridgingRejectsSomethingJSONCannotRepresent() {
        XCTAssertNil(JSONValue(any: Date()))
        XCTAssertNil(JSONValue(any: ["ok": 1, "bad": Date()] as [String: Any]))
    }

    func testAnyValueIsSerializable() throws {
        let value: JSONValue = .object(["a": .int(1), "b": .array([.string("x")]), "c": .null])
        let any = value.anyValue
        XCTAssertTrue(JSONSerialization.isValidJSONObject(any))
        let data = try JSONSerialization.data(withJSONObject: any)
        XCTAssertEqual(try decoder.decode(JSONValue.self, from: data), value)
    }

    // MARK: - Hashing

    func testEqualValuesHashTogether() {
        let first: JSONValue = .object(["metric": .string("ts_pct"), "limit": .int(10)])
        let second: JSONValue = .object(["limit": .int(10), "metric": .string("ts_pct")])
        XCTAssertEqual(first, second)
        XCTAssertEqual(first.hashValue, second.hashValue)
        XCTAssertEqual(Set([first, second]).count, 1)
    }

    // MARK: - AnyCodingKey

    func testAnyCodingKeyCarriesBothFormsOfKey() {
        let named = AnyCodingKey(stringValue: "payload")
        XCTAssertEqual(named.stringValue, "payload")
        XCTAssertNil(named.intValue)
        let indexed = AnyCodingKey(intValue: 3)
        XCTAssertEqual(indexed.intValue, 3)
        XCTAssertEqual(indexed.stringValue, "3")
        XCTAssertEqual(AnyCodingKey(named), named)
    }
}
