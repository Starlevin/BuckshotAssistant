extends SceneTree
# Read-only observer. Never exports shell ordering or changes game state.
var elapsed = 0.0
var spawner = null
var seen_nonempty = false
var session = str(OS.get_process_id()) + "-" + str(Time.get_unix_time_from_system())

func _initialize():
	call_deferred("start_original")

func start_original():
	var scene_path = ProjectSettings.get_setting("application/run/main_scene", "res://scenes/menu.tscn")
	change_scene_to_file(scene_path)

func find_spawner(node):
	var script = node.get_script()
	if script != null and script.resource_path == "res://scripts/ShellSpawner.gd":
		return node
	for child in node.get_children():
		var found = find_spawner(child)
		if found != null:
			return found
	return null

func _process(delta):
	elapsed += delta
	if elapsed < 0.1:
		return false
	elapsed = 0.0
	if not is_instance_valid(spawner):
		spawner = find_spawner(root)
		seen_nonempty = false
	var data = {"schema": 1, "pid": OS.get_process_id(), "session": session, "time": Time.get_unix_time_from_system(), "valid": false, "status": "waiting", "source": "game-sequence"}
	if is_instance_valid(spawner):
		var rounds = spawner.get("sequenceArray")
		if rounds != null:
			if rounds.size() > 0:
				seen_nonempty = true
			if seen_nonempty:
				data["valid"] = true
				data["status"] = "active" if rounds.size() > 0 else "empty"
				data["live"] = rounds.count("live")
				data["blank"] = rounds.count("blank")
				data["total"] = rounds.size()
	var file = FileAccess.open("user://buckshot_assistant_state.json", FileAccess.WRITE)
	if file:
		file.store_string(JSON.stringify(data))
		file.close()
	return false
