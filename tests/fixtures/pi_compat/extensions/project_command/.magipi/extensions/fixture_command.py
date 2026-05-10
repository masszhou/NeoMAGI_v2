def setup(api):
    def run(ctx):
        api.append_entry("fixture_command", {"args": ctx.get("args", [])})

    api.register_command(
        "fixture_status",
        {
            "description": "Emit fixture command status",
            "handler": run,
        },
    )
