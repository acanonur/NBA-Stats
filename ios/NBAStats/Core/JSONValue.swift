import Foundation

/// A fully decoded JSON value.
///
/// Widget configuration is schema driven at runtime (`contracts/widgets.json` describes the
/// fields, not a Swift struct), so configuration values travel as `JSONValue` rather than as a
/// fixed type. The same enum is used for the metric dictionaries the API returns, where a `null`
/// means "this stat did not exist for this subject's era" and must never be rendered as zero.
///
/// Decoding order is `null`, `Bool`, `Int`, `Double`, `String`, array, object. `Bool` is tried
/// before the numeric cases so a JSON `true` never becomes `1`, and `Int` before `Double` so that
/// `10` round-trips as `.int(10)` and the bundled fixtures re-encode equivalently.
public enum JSONValue: Codable, Hashable, Sendable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    // MARK: Accessors

    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var intValue: Int? {
        switch self {
        case .int(let value):
            return value
        case .double(let value):
            // Only an exactly representable double is an integer: 12.0 is, 12.5 is not.
            return Int(exactly: value)
        default:
            return nil
        }
    }

    /// Returns a value for `.int` as well, so numeric config and metric values read uniformly.
    public var doubleValue: Double? {
        switch self {
        case .double(let value):
            return value
        case .int(let value):
            return Double(value)
        default:
            return nil
        }
    }

    public var boolValue: Bool? {
        switch self {
        case .bool(let value):
            return value
        case .int(let value):
            // Tolerates a server that encodes a flag as 0/1.
            if value == 0 { return false }
            if value == 1 { return true }
            return nil
        default:
            return nil
        }
    }

    public var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    public var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    public var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    /// The elements of an array of strings, or `nil` when this is not such an array.
    public var stringArrayValue: [String]? {
        guard case .array(let elements) = self else { return nil }
        var result: [String] = []
        result.reserveCapacity(elements.count)
        for element in elements {
            guard let string = element.stringValue else { return nil }
            result.append(string)
        }
        return result
    }

    /// The elements of an array of integers, or `nil` when this is not such an array.
    public var intArrayValue: [Int]? {
        guard case .array(let elements) = self else { return nil }
        var result: [Int] = []
        result.reserveCapacity(elements.count)
        for element in elements {
            guard let value = element.intValue else { return nil }
            result.append(value)
        }
        return result
    }

    /// Object member access; `nil` for every non-object case.
    public subscript(key: String) -> JSONValue? {
        guard case .object(let members) = self else { return nil }
        return members[key]
    }

    /// Array element access; `nil` for every non-array case and for an out-of-range index.
    public subscript(index: Int) -> JSONValue? {
        guard case .array(let elements) = self, index >= 0, index < elements.count else { return nil }
        return elements[index]
    }

    // MARK: Codable

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
            return
        }
        if let value = try? container.decode(Bool.self) {
            self = .bool(value)
            return
        }
        if let value = try? container.decode(Int.self) {
            self = .int(value)
            return
        }
        if let value = try? container.decode(Double.self) {
            self = .double(value)
            return
        }
        if let value = try? container.decode(String.self) {
            self = .string(value)
            return
        }
        if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
            return
        }
        if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
            return
        }
        throw DecodingError.dataCorruptedError(
            in: container,
            debugDescription: "Value is not representable as JSON."
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .int(let value):
            try container.encode(value)
        case .double(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    // MARK: Bridging

    /// Bridges a value produced by `JSONSerialization` (or a plain Swift literal tree).
    /// Returns `nil` when the tree contains something JSON cannot represent.
    public init?(any value: Any) {
        switch value {
        case is NSNull:
            self = .null
        case let number as NSNumber:
            // NSNumber erases Bool, Int and Double; CFBoolean identifies a genuine boolean, which
            // is the only way to stop `true` from arriving here as the number 1.
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                self = .bool(number.boolValue)
            } else {
                let asDouble = number.doubleValue
                if asDouble.isFinite, let asInt = Int(exactly: asDouble) {
                    self = .int(asInt)
                } else {
                    self = .double(asDouble)
                }
            }
        case let string as String:
            self = .string(string)
        case let array as [Any]:
            var elements: [JSONValue] = []
            elements.reserveCapacity(array.count)
            for element in array {
                guard let converted = JSONValue(any: element) else { return nil }
                elements.append(converted)
            }
            self = .array(elements)
        case let object as [String: Any]:
            var members: [String: JSONValue] = [:]
            for (key, element) in object {
                guard let converted = JSONValue(any: element) else { return nil }
                members[key] = converted
            }
            self = .object(members)
        default:
            return nil
        }
    }

    /// The `JSONSerialization`-compatible representation of this value.
    public var anyValue: Any {
        switch self {
        case .string(let value):
            return value
        case .int(let value):
            return value
        case .double(let value):
            return value
        case .bool(let value):
            return value
        case .array(let value):
            return value.map { $0.anyValue }
        case .object(let value):
            return value.mapValues { $0.anyValue }
        case .null:
            return NSNull()
        }
    }
}

// MARK: - Literals

extension JSONValue: ExpressibleByStringLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
}

extension JSONValue: ExpressibleByIntegerLiteral {
    public init(integerLiteral value: Int) { self = .int(value) }
}

extension JSONValue: ExpressibleByFloatLiteral {
    public init(floatLiteral value: Double) { self = .double(value) }
}

extension JSONValue: ExpressibleByBooleanLiteral {
    public init(booleanLiteral value: Bool) { self = .bool(value) }
}

extension JSONValue: ExpressibleByNilLiteral {
    public init(nilLiteral: ()) { self = .null }
}

extension JSONValue: ExpressibleByArrayLiteral {
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
}

extension JSONValue: ExpressibleByDictionaryLiteral {
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        var members: [String: JSONValue] = [:]
        for (key, value) in elements { members[key] = value }
        self = .object(members)
    }
}

extension JSONValue: CustomStringConvertible {
    public var description: String {
        switch self {
        case .string(let value):
            return value
        case .int(let value):
            return String(value)
        case .double(let value):
            return String(value)
        case .bool(let value):
            return value ? "true" : "false"
        case .array(let value):
            return "[" + value.map { $0.description }.joined(separator: ", ") + "]"
        case .object(let value):
            let members = value.keys.sorted().map { key -> String in
                let member = value[key]
                return "\(key): \(member?.description ?? "null")"
            }
            return "{" + members.joined(separator: ", ") + "}"
        case .null:
            return "null"
        }
    }
}

/// A `CodingKey` that can stand for any key, used where the key is only known at runtime.
public struct AnyCodingKey: CodingKey, Hashable, Sendable {
    public let stringValue: String
    public let intValue: Int?

    public init(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    public init(intValue: Int) {
        self.stringValue = String(intValue)
        self.intValue = intValue
    }

    public init(_ key: CodingKey) {
        self.stringValue = key.stringValue
        self.intValue = key.intValue
    }
}
