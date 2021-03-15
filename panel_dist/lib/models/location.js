import { View } from "@bokehjs/core/view";
import { Model } from "@bokehjs/model";
export class LocationView extends View {
    initialize() {
        super.initialize();
        this.model.pathname = window.location.pathname;
        this.model.search = window.location.search;
        this.model.hash = window.location.hash;
        // Readonly parameters on python side
        this.model.href = window.location.href;
        this.model.hostname = window.location.hostname;
        this.model.protocol = window.location.protocol;
        this.model.port = window.location.port;
    }
    connect_signals() {
        super.connect_signals();
        this.connect(this.model.properties.pathname.change, () => this.update('pathname'));
        this.connect(this.model.properties.search.change, () => this.update('search'));
        this.connect(this.model.properties.hash.change, () => this.update('hash'));
        this.connect(this.model.properties.reload.change, () => this.update('reload'));
    }
    update(change) {
        if (!this.model.reload || (change === 'reload')) {
            window.history.pushState({}, '', `${this.model.pathname}${this.model.search}${this.model.hash}`);
            this.model.href = window.location.href;
            if (change === 'reload')
                window.location.reload();
        }
        else {
            if (change == 'pathname')
                window.location.pathname = this.model.pathname;
            if (change == 'search')
                window.location.search = this.model.search;
            if (change == 'hash')
                window.location.hash = this.model.hash;
        }
    }
}
LocationView.__name__ = "LocationView";
export class Location extends Model {
    constructor(attrs) {
        super(attrs);
    }
    static init_Location() {
        this.prototype.default_view = LocationView;
        this.define(({ Boolean, String }) => ({
            href: [String, ""],
            hostname: [String, ""],
            pathname: [String, ""],
            protocol: [String, ""],
            port: [String, ""],
            search: [String, ""],
            hash: [String, ""],
            reload: [Boolean, false],
        }));
    }
}
Location.__name__ = "Location";
Location.__module__ = "panel.models.location";
Location.init_Location();
//# sourceMappingURL=location.js.map