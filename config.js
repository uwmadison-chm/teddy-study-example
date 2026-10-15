// Study settings. They're built into Teddy, so a change here needs a rebuild.
// See teddy/README.md for what each one does.
export default {
    // Pig's address.
    pigServer: "https://pig.example.edu",
    // The task this deployment sends data to; pig-task.toml has its entry.
    taskCode: "teddy_example",
    // While true, nothing is sent to pig; events are logged to the console, and
    // every screen shows a "Debug mode" banner. Set to false for real data.
    debug: true,
    // Hosts the end screen may send participants to with nextURL. Empty means
    // any http or https URL.
    nextUrlHosts: ["redcap.example.edu"],
    // Each module this study uses, and the CSV of its items, relative to this
    // folder. Links can only name these modules.
    modules: {
        sentence: "stimuli/sentence.csv",
        videolog: "stimuli/videolog.csv",
        reels: "stimuli/reels.csv",
        questions: "stimuli/questions.csv",
    },
    // How recordings are made. Leave any of these out to use the default shown.
    recording: {
        mimeTypes: ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"],
        videoBitsPerSecond: 1000000,
        audioBitsPerSecond: 64000,
        width: 640,
        height: 480,
        frameRate: 30,
        // The longest any one recording can run, in seconds, so a session left
        // open doesn't record for hours. Modules may stop their recordings sooner.
        maxSeconds: 600,
    },
};
