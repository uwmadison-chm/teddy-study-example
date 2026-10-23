# teddy-study-example

An example study for [Teddy](https://github.com/uwmadison-chm/teddy-static): its settings, its items, and the scripts that build and deploy it. Copy it to start a new study.

## What's here

```
config.js         Teddy's settings for this study
stimuli/
  sentences.csv   sentences for the sentences module
  videologs.csv   prompts for the videologs module
  questions.csv   questions for the questions module
  reels.csv       reels for the reels module; its file column is relative to stimuli/
  reels/*.mp4     the reel videos (two test patterns here)
pig-task.toml     this study's task entry for pig's configuration
teddy/            Teddy itself, as a git submodule
build.sh          builds Teddy with this study into teddy/dist/
dev.sh            runs Teddy's dev server with this study
deploy.sh         builds, then copies the build to the web server
prepare.py        turns organized pig data into CSVs and playable recordings
```

The `teddy` submodule pins the version of Teddy this study runs. Teddy's README describes [every setting in `config.js`](https://github.com/uwmadison-chm/teddy-static#configjs) and [the columns each CSV needs](https://github.com/uwmadison-chm/teddy-static#item-csvs).

## Getting started

```sh
git clone --recurse-submodules https://github.com/uwmadison-chm/teddy-study-example
cd teddy-study-example
./dev.sh
```

Then open http://localhost:9000/?modules=videologs:01|sentences:02|reels:01|questions:relaxed,energy to run a session. `debug` is on in `config.js`, so nothing is sent to pig and each event is logged to the browser console.

The scripts need Node and npm; `deploy.sh` also needs rsync and SSH access to the web server.

## Starting a new study from this one

1. Make a new repository from this one (on GitHub, "Use this template", or clone it and push it somewhere new).
2. Choose a task code for the study, lowercase, and set it as `taskCode` in `config.js` and in the `[task.…]` line of `pig-task.toml`. Task codes are unique across a pig server.
3. Set `pigServer` and `nextUrlHosts` in `config.js`.
4. Replace the CSVs and reels in `stimuli/` with the study's own, and list the modules it uses in `modules`. A module left out isn't in the build.
5. Set the `parameters` and `run_key` in `pig-task.toml` to what the study's links carry, then add the entry to the pig server's configuration and run `pig check` there.
6. Set the default `TARGET` in `deploy.sh` to the web server and directory.
7. When everything works, set `debug: false`.

`./build.sh` checks the study and lists anything wrong with it: a mistyped setting, a missing CSV column, a repeated ID, a reel file that isn't there.

## Deploying

```sh
./deploy.sh
```

This builds the study and copies `teddy/dist/` to the server with rsync. It copies new files before `index.html` and deletes old ones after, so a participant who loads the page mid-deploy gets one build or the other. `DEPLOY_TARGET=host:/path/ ./deploy.sh` sends it somewhere else.

It won't deploy a build with `debug: true`, since that build records nothing. `ALLOW_DEBUG=1 ./deploy.sh` deploys one anyway, for trying out a server.

If a study deploys differently (another server, a staging copy, extra steps before or after), copy `deploy.sh` to something like `mystudy-deploy.sh` and change what you need there. Each study repository is its own copy, so there's no need to make one script cover every case.

Serve it over HTTPS, since browsers only allow the camera on secure pages. [Teddy's README](https://github.com/uwmadison-chm/teddy-static#production-build) covers the caching headers and the gzip and brotli copies.

## Linking from REDCap

A link carries the pig task's parameters, the modules to run, and where to go afterwards:

```
https://teddy.example.edu/?participant_id=[record_id]&session=[event-name]&modules=videologs:01|sentences:00,01|reels:01,02&nextURL=https://redcap.example.edu/...
```

Teddy passes every parameter to pig when the session starts. `modules` lists the activities in order, separated by `|`, each with its item IDs after a colon. [Teddy's README](https://github.com/uwmadison-chm/teddy-static#linking-to-teddy) has the details.

## Preparing the data

Pig stores what Teddy sends as it arrives: events as JSON lines, recordings in numbered parts. `pig organize`, on the machine where the data ends up, copies each finished run into a tree by task, participant, and session. `prepare.py` takes it from there:

```sh
python3 prepare.py /secure/pig/organized/teddy_example prepared
```

For each run, at the same path under `prepared/`, it writes `events.csv` and one playable file per recording. `prepared/events.csv` has every run's rows together. It needs Python 3 and ffmpeg, and never changes the organized tree. Running it again rewrites the CSVs and joins only the recordings that aren't there yet; `--force` joins them all again.

`events.csv` has one row per event, in the order Teddy made them, with repeats dropped:

- The run's `task_code`, `run_number`, `run_id`, and `disposition` (`complete`, or `expired` if pig closed the run before Teddy finished it), and its link parameters, on every row. A parameter that has the same name as an event field gets a `param.` prefix, like `param.module`.
- Then each event's fields. Nested ones are flattened into dotted columns (`study.hash`, `_client.wall_time`); lists stay in one cell, as JSON (`choices`, `items`). An event without a field leaves its cell empty.
- `recording_ms`: for an event during a recording, how far into that recording's video it happened.
- On each `recording` row, `media_file` (the joined file, relative to the CSV), `media_finished` (`false` when the tab closed mid-recording, so the file ends early), and `media_duration_s`.

Recordings are named for their number, kind, and item, like `rec03-sentence-02.webm`, with the extension from the browser's content type (`.webm` from most browsers, `.mp4` from Safari).

It reports anything it couldn't handle (a run whose event schema it doesn't know, a recording ffmpeg can't read, parts missing) and exits with an error, so a scheduled job notices. [Teddy's docs/events.md](https://github.com/uwmadison-chm/teddy-static/blob/main/docs/events.md) describes every event and field.

It's an example: copy it with the rest of this repository and change it to suit the study's analysis.

## Updating Teddy

The submodule stays on the commit this repository records until it's moved:

```sh
cd teddy
git fetch
git checkout <commit or tag>
cd ..
./build.sh
git commit -am "Update Teddy"
```

Changes to Teddy that one study needs belong in Teddy's repository, so every study can pick them up.
