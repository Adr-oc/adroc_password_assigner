/** @odoo-module */
import { ListController } from "@web/views/list/list_controller";
import { registry } from '@web/core/registry';
import { listView } from '@web/views/list/list_view';
import { useService } from "@web/core/utils/hooks";

export class PasswordAssignerListController extends ListController {
    setup() {
        super.setup();
        this.action = useService("action");
    }

    onAssignPasswordsClick() {
        this.action.doAction({
            type: 'ir.actions.act_window',
            res_model: 'password.assigner.wizard',
            name: 'Asignar Contraseñas de Pago',
            view_mode: 'form',
            views: [[false, 'form']],
            target: 'new',
            res_id: false,
            context: {},
        });
    }
}

registry.category("views").add("password_assigner_list_button", {
    ...listView,
    Controller: PasswordAssignerListController,
    buttonTemplate: "adroc_password_assigner.ListView.Buttons",
});
