// scripts/voice-actions.js
// Injected via: python3 devtools-eval.py -f scripts/voice-actions.js
// Hangs helper functions off window.__voiceIntegration for the orchestrator
// to call one at a time via one-shot devtools-eval invocations.
(function () {
    const { UserStore, ChannelStore, PermissionStore, PermissionsBits, SelectedChannelStore, ChannelRouter } =
        Vencord.Webpack.Common;
    const { selectVoiceChannel } = Vencord.Webpack.findByProps("selectVoiceChannel", "selectChannel");

    function hasLoggedInUserWithAccess(channelId) {
        const user = UserStore && UserStore.getCurrentUser && UserStore.getCurrentUser();
        if (!user) return false;
        const channel = ChannelStore && ChannelStore.getChannel && ChannelStore.getChannel(channelId);
        if (!channel) return false;
        return !!(
            PermissionStore.can(PermissionsBits.VIEW_CHANNEL, channel) &&
            PermissionStore.can(PermissionsBits.CONNECT, channel)
        );
    }

    function joinVoiceChannel(channelId) {
        ChannelRouter.transitionToChannel(channelId);
        selectVoiceChannel(channelId);
        return true;
    }

    function leaveVoiceChannel() {
        selectVoiceChannel(null);
        return true;
    }

    function currentVoiceChannelId() {
        return SelectedChannelStore.getVoiceChannelId() || null;
    }

    window.__voiceIntegration = { hasLoggedInUserWithAccess, joinVoiceChannel, leaveVoiceChannel, currentVoiceChannelId };
    return true;
})();
