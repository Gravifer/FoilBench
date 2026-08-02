function _schema_failure(path::AbstractString, message::AbstractString)
    throw(ArgumentError("JSON schema violation at $path: $message"))
end

function _matches_json_type(value, selected_type::AbstractString)
    selected_type == "object" && return value isa AbstractDict
    selected_type == "array" && return value isa AbstractVector
    selected_type == "string" && return value isa AbstractString
    selected_type == "boolean" && return value isa Bool
    selected_type == "integer" && return value isa Integer && !(value isa Bool)
    selected_type == "number" && return value isa Real && !(value isa Bool)
    selected_type == "null" && return value === nothing
    return false
end

function _resolve_local_reference(root::AbstractDict, reference::AbstractString)
    startswith(reference, "#/") ||
        _schema_failure("\$", "only local JSON schema references are supported")
    selected = root
    for encoded in split(reference[3:end], '/')
        token = replace(replace(encoded, "~1" => "/"), "~0" => "~")
        selected isa AbstractDict && haskey(selected, token) ||
            _schema_failure("\$", "unresolved JSON schema reference $reference")
        selected = selected[token]
    end
    return selected
end

function _schema_matches(value, schema, path::String, root::AbstractDict)
    try
        _validate_json_schema(value, schema, path, root)
        return true
    catch error
        error isa ArgumentError || rethrow()
        return false
    end
end

function _validate_json_schema(value, schema, path::String, root::AbstractDict)
    schema === true && return nothing
    schema === false && _schema_failure(path, "value is forbidden")
    schema isa AbstractDict || _schema_failure(path, "schema must be an object or boolean")

    if haskey(schema, "\$ref")
        _validate_json_schema(
            value,
            _resolve_local_reference(root, String(schema["\$ref"])),
            path,
            root,
        )
    end
    if haskey(schema, "allOf")
        for branch in schema["allOf"]
            _validate_json_schema(value, branch, path, root)
        end
    end
    if haskey(schema, "anyOf")
        any(branch -> _schema_matches(value, branch, path, root), schema["anyOf"]) ||
            _schema_failure(path, "does not match any allowed schema")
    end
    if haskey(schema, "oneOf")
        matches = count(branch -> _schema_matches(value, branch, path, root), schema["oneOf"])
        matches == 1 || _schema_failure(path, "does not match exactly one allowed schema")
    end
    if haskey(schema, "if")
        branch_name = _schema_matches(value, schema["if"], path, root) ? "then" : "else"
        haskey(schema, branch_name) &&
            _validate_json_schema(value, schema[branch_name], path, root)
    end

    if haskey(schema, "type")
        selected_type = schema["type"]
        selected_type isa AbstractString ||
            _schema_failure(path, "unsupported non-string type declaration")
        _matches_json_type(value, selected_type) ||
            _schema_failure(path, "expected JSON type $selected_type")
    end
    haskey(schema, "const") && !isequal(value, schema["const"]) &&
        _schema_failure(path, "does not equal the required constant")
    if haskey(schema, "enum")
        any(candidate -> isequal(value, candidate), schema["enum"]) ||
            _schema_failure(path, "is not one of the allowed values")
    end

    if value isa AbstractDict
        if haskey(schema, "required")
            for required_name in schema["required"]
                haskey(value, required_name) ||
                    _schema_failure(path, "missing required property $required_name")
            end
        end
        properties = get(schema, "properties", Dict{String,Any}())
        additional = get(schema, "additionalProperties", true)
        for (name, child) in value
            child_path = "$path.$name"
            if haskey(properties, name)
                _validate_json_schema(child, properties[name], child_path, root)
            elseif additional === false
                _schema_failure(path, "unexpected property $name")
            elseif additional isa AbstractDict || additional isa Bool
                _validate_json_schema(child, additional, child_path, root)
            end
        end
    end

    if value isa AbstractVector
        length(value) >= get(schema, "minItems", 0) ||
            _schema_failure(path, "contains too few items")
        length(value) <= get(schema, "maxItems", typemax(Int)) ||
            _schema_failure(path, "contains too many items")
        if get(schema, "uniqueItems", false)
            for left in eachindex(value), right in eachindex(value)
                left < right && isequal(value[left], value[right]) &&
                    _schema_failure(path, "contains duplicate items")
            end
        end
        prefix_items = get(schema, "prefixItems", Any[])
        for index in 1:min(length(value), length(prefix_items))
            _validate_json_schema(value[index], prefix_items[index], "$path[$index]", root)
        end
        if haskey(schema, "items")
            first_item = isempty(prefix_items) ? 1 : length(prefix_items) + 1
            for index in first_item:length(value)
                _validate_json_schema(value[index], schema["items"], "$path[$index]", root)
            end
        end
    end

    if value isa Real && !(value isa Bool)
        haskey(schema, "minimum") && value < schema["minimum"] &&
            _schema_failure(path, "is below the minimum")
        haskey(schema, "exclusiveMinimum") && value <= schema["exclusiveMinimum"] &&
            _schema_failure(path, "is not above the exclusive minimum")
        haskey(schema, "maximum") && value > schema["maximum"] &&
            _schema_failure(path, "is above the maximum")
    end
    if value isa AbstractString
        length(value) >= get(schema, "minLength", 0) ||
            _schema_failure(path, "is shorter than the minimum length")
        haskey(schema, "pattern") && !occursin(Regex(schema["pattern"]), value) &&
            _schema_failure(path, "does not match the required pattern")
    end
    return nothing
end

function validate_json_schema(value, schema::AbstractDict)
    _validate_json_schema(value, schema, "\$", schema)
    return nothing
end

function validate_json_file(document::AbstractDict, schema_path::AbstractString)
    schema = JSON3.read(read(schema_path, String), Dict{String,Any})
    return validate_json_schema(document, schema)
end
